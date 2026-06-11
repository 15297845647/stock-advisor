"""养家选股编排 — 两阶段筛选 → Top N 串联技术分析 → 操作纪律拼装

阶段A：全量行情按量比粗筛活跃候选池（1次请求）
阶段B：候选逐只拉K线，过养家规则（涨停回溯+排除连板+站稳5日线）
阶段C：入选 Top N 走深度分析管线（结论版），附操作纪律提示文本
"""

import logging

from application.analysis_service import AnalysisService
from application.config_service import ConfigService
from domain.models.user_context import UserContext
from domain.strategy import yangjia_screener
from domain.strategy.strategy_config import YangjiaConfig
from domain.stock_analyzer import analyze_technical
from infrastructure.akshare_client import AKShareClient
from repository.user_repository import UserRepository

logger = logging.getLogger(__name__)

# 拉K线天数：覆盖回溯窗口同时满足技术指标计算（analyze_technical 需 >=10 根）
_HISTORY_DAYS = 60


class StockPickerService:
    def __init__(self):
        self.akshare = AKShareClient()
        self.analysis = AnalysisService()
        self.config_service = ConfigService()
        self.user_repo = UserRepository()

    async def pick(self, ctx: UserContext, wechat_id: str) -> str:
        """养家策略选股主流程"""
        cfg = await self.config_service.get_yangjia_config()

        # 阶段A：活跃候选池
        pool = await self.akshare.get_active_pool(cfg.volume_ratio_min, cfg.candidate_cap)
        if not pool:
            return "暂时无法获取行情数据，请稍后再试。"

        # 阶段B：K线规则筛选
        winners = await self._screen_pool(pool, cfg)
        if not winners:
            return self._no_match_message(cfg)

        picks = winners[:cfg.output_count]

        if cfg.auto_watchlist:
            await self._add_to_watchlist(wechat_id, picks)

        # 阶段C：Top N 深度分析（结论版）
        deep_reports = await self._deep_analyze(picks)

        return self._assemble(picks, deep_reports, cfg)

    async def _screen_pool(self, pool: list[dict], cfg: YangjiaConfig) -> list[dict]:
        """逐只候选拉K线，过养家规则，返回入选标的（保留量比排序）"""
        winners = []
        for candidate in pool:
            code = candidate["code"]
            try:
                bars = await self.akshare.get_stock_history(code, days=_HISTORY_DAYS)
            except Exception as e:
                logger.warning("拉取 %s K线失败: %s", code, e)
                continue
            if not bars:
                continue

            tech = analyze_technical(bars)
            ma5 = tech.ma5 if tech else None
            result = yangjia_screener.screen(bars, code, candidate["price"], ma5, cfg)
            if result.passed:
                winners.append({**candidate, "boards": result.consecutive_boards})
        return winners

    async def _add_to_watchlist(self, wechat_id: str, picks: list[dict]) -> None:
        """规则1：入选标的加入自选"""
        for p in picks:
            try:
                await self.user_repo.subscribe(wechat_id, p["code"], p["name"])
            except Exception as e:
                logger.warning("加自选 %s 失败: %s", p["code"], e)

    async def _deep_analyze(self, picks: list[dict]) -> list[str]:
        """对入选标的逐只跑深度分析管线，跳过失败的"""
        reports = []
        for p in picks:
            try:
                reports.append(await self.analysis.analyze_stock_deep(p["code"]))
            except Exception as e:
                logger.warning("深度分析 %s 失败: %s", p["code"], e)
        return reports

    @staticmethod
    def _no_match_message(cfg: YangjiaConfig) -> str:
        return (
            f"养家选股：当前市场按「近{cfg.lookback_days}天涨停 + 量比≥{cfg.volume_ratio_min} "
            f"+ 站稳5日线，排除≥{cfg.max_boards}连板」未筛到合适标的，"
            f"可能行情不活跃，建议空仓等待。"
        )

    @staticmethod
    def _assemble(picks: list[dict], deep_reports: list[str], cfg: YangjiaConfig) -> str:
        """拼装最终报告：入选清单 + 深度分析 + 操作纪律"""
        sep = "\n\n" + "=" * 30 + "\n\n"

        header = [
            "🎯 养家选股（最笨的方法）",
            f"筛选：近{cfg.lookback_days}天涨停 + 量比≥{cfg.volume_ratio_min} + 站稳5日线，"
            f"排除≥{cfg.max_boards}连板\n",
            f"入选 {len(picks)} 只：",
        ]
        for i, p in enumerate(picks, 1):
            header.append(
                f"  {i}. {p['name']}（{p['code']}）"
                f" 现价{p['price']} 量比{p['volume_ratio']:.1f}"
                f" {p['boards']}连板"
            )

        parts = ["\n".join(header)]
        if deep_reports:
            parts.append(sep + "入选标的深度分析：")
            parts.extend(sep + r for r in deep_reports)

        discipline = sep + "\n".join([
            "📏 操作纪律：",
            f"1. {cfg.advice_rule3}",
            f"2. {cfg.advice_rule4}",
            f"3. {cfg.advice_rule5}",
            "\n以上仅供参考，不构成投资建议，投资有风险，入市需谨慎。",
        ])
        parts.append(discipline)
        return "\n".join(parts)
