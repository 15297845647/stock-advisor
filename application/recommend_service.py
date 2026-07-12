"""推荐流程总编排 — 6 Layer 串联

流程：
    Layer 1  意图解析              (domain.RecommendIntentParser)
    Layer 2  硬规则筛选            (domain.RecommendScreener)
    Layer 3  板块热度加权          (domain.SectorHeatCalculator)
    Layer 4  技术评分（并发拉数据） (domain.TechnicalScorer)
    Layer 5  LLM 语义裁决          (LLMRouter)
    Layer 6  决策校准 + 落库

单一职责：编排各 Layer，不含具体规则。
"""

import asyncio
import logging
from pathlib import Path

from domain.models.candidate import Candidate
from domain.models.llm_task import LLMRequest, LLMTaskType
from domain.models.recommend_intent import RecommendIntent
from domain.models.recommendation import Recommendation
from domain.models.user_context import UserContext
from domain.recommend_intent_parser import RecommendIntentParser
from domain.recommend_screener import RecommendScreener
from domain.recommendation_parser import RecommendationParser
from domain.sector_heat import SectorHeatCalculator
from domain.technical_scorer import TechnicalScorer
from infrastructure.akshare_client import AKShareClient, _get_spot_df
from infrastructure.data_source import get_data_manager
from infrastructure.llm import get_llm_router
from repository.recommend_repository import RecommendRepository

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class RecommendService:
    """推荐流程总编排"""

    def __init__(self):
        self.intent_parser = RecommendIntentParser()
        self.screener = RecommendScreener()
        self.sector_heat = SectorHeatCalculator()
        self.scorer = TechnicalScorer()
        self.rec_parser = RecommendationParser()
        self.repo = RecommendRepository()
        self.akshare = AKShareClient()

    async def recommend(
        self, message: str, ctx: UserContext,
    ) -> tuple[list[Recommendation], str]:
        """
        主入口：返回 (推荐列表, LLM 总结文本)
        失败时返回空列表 + 错误说明。
        """
        wechat_id = ctx.profile.wechat_id if ctx.profile else None

        # Layer 1: 意图解析
        exclude = await self._resolve_exclude_codes(wechat_id, message)
        intent = self.intent_parser.parse(message, ctx, exclude_codes=exclude)
        logger.info(
            "推荐意图: style=%s sectors=%s target=%d exclude=%d",
            intent.style, intent.sectors, intent.target_count, len(exclude),
        )

        # Layer 2-4: 三步筛选
        candidates = await self._pipeline_filter(intent)
        if not candidates:
            return [], "本次未筛选到合适标的，请稍后再试或换个诉求。"

        # Layer 5: LLM 裁决
        recs, summary = await self._llm_judge(candidates, intent, wechat_id)
        if not recs:
            return [], summary or "LLM 未返回有效推荐。"

        # Layer 6: 校准 + 落库
        recs = self._apply_calibration(recs, candidates)
        await self._persist(wechat_id, recs, intent)

        return recs, summary

    # ────────────── Layer 2-4 ──────────────

    async def _pipeline_filter(
        self, intent: RecommendIntent,
    ) -> list[Candidate]:
        """并发跑 Layer 2/3/4，返回带完整评分的 Top N 候选"""
        # Layer 2: 硬规则筛选（用全量行情快照）
        spot_df = await _get_spot_df()
        pool = self.screener.screen(spot_df, intent)
        logger.info("Layer2 硬规则筛选: %d 只候选", len(pool))

        if not pool:
            return []

        # Layer 3: 板块热度加权
        sector_flows = await self._fetch_sector_flows()
        pool = self.sector_heat.score_candidates(pool, sector_flows, intent)
        top30 = pool[: min(30, len(pool))]
        logger.info("Layer3 板块加权后 Top30 已选定")

        # Layer 4: 技术评分（并发拉 kline + fund_flow）
        scored = await self._score_technicals(top30)
        scored.sort(key=lambda c: (c.tech_total, c.sector_score), reverse=True)
        top10 = scored[: min(10, len(scored))]
        logger.info("Layer4 技术评分完成 Top10")
        return top10

    async def _fetch_sector_flows(self) -> list[dict]:
        """拉板块资金流（走 DataSourceManager）"""
        result = await get_data_manager().fetch_sector_flow(count=30)
        return result.data if result.success else []

    async def _score_technicals(
        self, candidates: list[Candidate],
    ) -> list[Candidate]:
        """并发为每个候选拉 kline+flow 并打分（限流 5 并发）"""
        sem = asyncio.Semaphore(5)

        async def _process(c: Candidate) -> Candidate:
            async with sem:
                bars_result = await get_data_manager().fetch_kline(c.code, days=30)
                bars = bars_result.data if bars_result.success else []

                flow_result = await get_data_manager().fetch_fund_flow(c.code)
                flows = flow_result.data if flow_result.success else []

                return self.scorer.score(c, bars, flows)

        tasks = [_process(c) for c in candidates]
        return await asyncio.gather(*tasks)

    # ────────────── Layer 5 ──────────────

    async def _llm_judge(
        self, candidates: list[Candidate], intent: RecommendIntent,
        wechat_id: str | None,
    ) -> tuple[list[Recommendation], str]:
        """喂 Top10 数据 + 用户诉求，让 LLM 裁决"""
        prompt_tmpl = (_PROMPTS_DIR / "recommend_judge.txt").read_text(encoding="utf-8")
        system_prompt = prompt_tmpl.format(target_count=intent.target_count)

        user_content = self._build_llm_input(candidates, intent)

        req = LLMRequest(
            task_type=LLMTaskType.RECOMMEND_JUDGE,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_content}],
            max_tokens=4096,
            wechat_id=wechat_id,
        )

        try:
            resp = await get_llm_router().chat(req)
        except Exception as e:
            logger.exception("LLM 裁决失败")
            return [], f"LLM 调用失败: {e}"

        idx = {c.code: c for c in candidates}
        recs = self.rec_parser.parse(resp.content, idx)
        summary = self.rec_parser.extract_summary_text(resp.content)
        return recs, summary

    @staticmethod
    def _build_llm_input(
        candidates: list[Candidate], intent: RecommendIntent,
    ) -> str:
        """构造喂给 LLM 的完整上下文"""
        lines = [f"【用户诉求】{intent.raw_message}"]
        lines.append(f"【风格】{intent.style}  【持有周期】{intent.horizon_days}天")

        if intent.sectors:
            lines.append(f"【目标板块】{', '.join(intent.sectors)}")
        if intent.blacklist_sectors:
            lines.append(f"【禁忌板块】{', '.join(intent.blacklist_sectors)}")
        if intent.exclude_codes:
            lines.append(f"【本次排除】{', '.join(intent.exclude_codes[:10])}")

        lines.append("\n【候选股票 Top10（已完成硬筛+板块+技术评分）】")
        for i, c in enumerate(candidates, 1):
            lines.append(
                f"\n{i}. {c.name}({c.code}) 价{c.price} 涨{c.change_pct:+.2f}%"
            )
            lines.append(
                f"   换手{c.turnover:.1f}% 量比{c.volume_ratio:.1f} "
                f"成交额{c.amount / 1e8:.1f}亿"
            )
            if c.sector:
                lines.append(f"   板块={c.sector}(热度{c.sector_score:.0f}分)")
            lines.append(f"   技术总分={c.tech_total:.0f}: {c.kline_summary}")
            lines.append(f"   {c.fund_flow_summary}")

        return "\n".join(lines)

    # ────────────── Layer 6 ──────────────

    def _apply_calibration(
        self,
        recs: list[Recommendation],
        candidates: list[Candidate],
    ) -> list[Recommendation]:
        """
        决策校准 — 目前仅做简单合理性检查：
        - target 不高于当前价 * 1.3
        - stop 不低于当前价 * 0.85
        - risk_score 归一到 1-10
        """
        for rec in recs:
            if rec.price <= 0:
                continue

            max_target = rec.price * 1.3
            min_stop = rec.price * 0.85

            if rec.target_price > max_target:
                rec.target_price = round(max_target, 2)
                rec.adjusted = True
            if rec.stop_loss < min_stop:
                rec.stop_loss = round(min_stop, 2)
                rec.adjusted = True
            if rec.stop_loss > rec.price:
                # 止损不能高于当前价
                rec.stop_loss = round(rec.price * 0.95, 2)
                rec.adjusted = True

            rec.risk_score = max(1, min(10, rec.risk_score))
        return recs

    async def _persist(
        self, wechat_id: str | None, recs: list[Recommendation],
        intent: RecommendIntent,
    ) -> None:
        """落库"""
        try:
            await self.repo.insert_batch(wechat_id, recs, intent.to_dict())
        except Exception as e:
            logger.warning("推荐落库失败(忽略): %s", e)

    async def _resolve_exclude_codes(
        self, wechat_id: str | None, message: str,
    ) -> list[str]:
        """判断是不是"再推一批"，是则查最近推过的做去重"""
        if not wechat_id:
            return []
        if not self.intent_parser.is_refresh_request(message):
            return []
        try:
            return await self.repo.recent_codes(wechat_id, hours=24)
        except Exception:
            return []

    # ────────────── 输出格式化 ──────────────

    @staticmethod
    def format_response(
        recs: list[Recommendation], summary: str,
    ) -> str:
        """把推荐列表格式化为微信消息"""
        if not recs:
            return summary or "本次未筛选到合适标的。"

        lines: list[str] = []
        if summary:
            lines.append(summary.strip())
            lines.append("")

        for i, r in enumerate(recs, 1):
            lines.append(f"{i}. {r.name}（{r.code}）")
            lines.append(f"   现价 {r.price}")
            lines.append(
                f"   目标 {r.target_price} | 止损 {r.stop_loss} | 风险 {r.risk_score}/10"
            )
            if r.reason:
                lines.append(f"   {r.reason}")
            if r.adjusted:
                lines.append("   （目标/止损已规则校准）")
            lines.append("")

        lines.append("💡 数据仅供参考，投资有风险，请独立决策。")
        return "\n".join(lines).strip()
