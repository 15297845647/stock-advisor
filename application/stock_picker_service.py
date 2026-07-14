"""选股编排 — 按用户交易风格分派策略（养家短线 / 中长线）

通用流程：候选池 → 逐只规则筛选 → Top N 深度分析(结论版) → 操作纪律拼装。
- 养家短线：量比活跃池 + 涨停回溯/排连板/站稳5日线
- 中长线：均线多头 + 趋势向上 + 基本面(ROE/PE/营收增长)
"""

import logging
from datetime import date

from application.analysis_service import AnalysisService
from application.config_service import ConfigService
from domain.models.user_context import UserContext
from domain.prompt_builder import build_system_prompt
from domain.strategy import midlong_screener, yangjia_screener
from domain.strategy.strategy_config import MidLongConfig, YangjiaConfig
from domain.stock_analyzer import TechnicalSnapshot, analyze_technical
from infrastructure.akshare_client import AKShareClient
from infrastructure.minimax_client import MiniMaxClient
logger = logging.getLogger(__name__)

# 拉K线天数：覆盖回溯窗口同时满足技术指标计算（analyze_technical 需 >=10 根）
_HISTORY_DAYS = 60

# 交易风格 → 策略映射（day/swing 走养家短线，long/position 走中长线）
_STYLE_STRATEGY = {
    "day": "yangjia", "swing": "yangjia",
    "long": "midlong", "position": "midlong",
}


def resolve_strategy(trade_style: str) -> str:
    """按用户交易风格解析选股策略，未知风格默认养家短线"""
    return _STYLE_STRATEGY.get(trade_style, "yangjia")


# 每用户今日已推荐代码，支持"再推一批"去重：wechat_id -> (date, [codes])
_recent_picks: dict[str, tuple] = {}


class StockPickerService:
    def __init__(self):
        self.akshare = AKShareClient()
        self.analysis = AnalysisService()
        self.config_service = ConfigService()
        self.minimax = MiniMaxClient()

    async def pick(self, ctx: UserContext, wechat_id: str, count: int | None = None) -> str:
        """按用户交易风格分派到对应选股策略；count 为本次期望条数"""
        strategy = resolve_strategy(ctx.profile.trade_style)
        logger.info("用户 %s 交易风格=%s → 策略=%s", wechat_id, ctx.profile.trade_style, strategy)
        if strategy == "midlong":
            return await self._pick_midlong(wechat_id, count)
        return await self._pick_yangjia(wechat_id, count)

    def _select_new_picks(self, wechat_id: str, winners: list[dict], count: int) -> list[dict]:
        """排除今日已推荐的标的，取下一批；全部推完则重置从头循环"""
        today = date.today()
        rec = _recent_picks.get(wechat_id)
        shown = list(rec[1]) if rec and rec[0] == today else []

        fresh = [w for w in winners if w["code"] not in shown]
        if not fresh:  # 已轮完一遍，重置重新推
            shown = []
            fresh = winners

        picks = fresh[:count]
        shown.extend(p["code"] for p in picks)
        _recent_picks[wechat_id] = (today, shown)
        return picks

    async def _pick_yangjia(self, wechat_id: str, count: int | None = None) -> str:
        """养家短线选股主流程"""
        cfg = await self.config_service.get_yangjia_config()

        # 阶段A：活跃候选池
        pool = await self.akshare.get_active_pool(cfg.volume_ratio_min, cfg.candidate_cap)
        if not pool:
            return "暂时无法获取行情数据，请稍后再试。"

        # 阶段B：K线规则筛选
        winners = await self._screen_pool(pool, cfg)
        if not winners:
            return self._no_match_message(cfg)

        picks = self._select_new_picks(wechat_id, winners, count or cfg.output_count)

        # 阶段C：快速汇总（1次LLM，不跑完整agent管线）
        verdicts = await self._quick_verdicts(picks, "养家短线")

        return self._assemble(picks, verdicts, cfg)

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
                winners.append({
                    **candidate,
                    "boards": result.consecutive_boards,
                    "vr": result.volume_ratio,
                    "signal": self._tech_signal(tech),
                })
        return winners

    async def _quick_verdicts(self, picks: list[dict], strategy_label: str) -> str:
        """一次 LLM 汇总，为入选股各出一句话操作建议（不跑 agent 管线）"""
        if not picks:
            return ""

        lines = []
        for p in picks:
            lines.append(
                f"{p['name']}（{p['code']}）现价{p['price']} "
                f"涨跌{p['change_pct']:+.2f}% {p.get('signal', '')}"
            )
        data = "\n".join(lines)
        prompt = (
            f"以下是通过「{strategy_label}」策略筛选出的股票及技术面数据。"
            f"请为每只股票用一句话给出操作建议（🟢买入/🟡持有/⚪观望 + 简要理由），"
            f"每只一行，简洁不啰嗦：\n\n{data}"
        )
        try:
            return await self.minimax.chat(
                system_prompt=build_system_prompt(),
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            logger.warning("快速汇总 LLM 失败: %s", e)
            return ""

    @staticmethod
    def _tech_signal(tech: TechnicalSnapshot | None) -> str:
        """压缩技术面信号（供快速汇总）"""
        if not tech:
            return "技术数据不足"
        return (
            f"趋势{tech.trend} MA5/10/20={tech.ma5}/{tech.ma10}/{tech.ma20} "
            f"RSI{tech.rsi_14:.0f} MACD柱{tech.macd_hist:+.3f} "
            f"支撑{tech.support}/压力{tech.resistance}"
        )

    @staticmethod
    def _no_match_message(cfg: YangjiaConfig) -> str:
        return (
            f"养家选股：当前市场按「近{cfg.lookback_days}天涨停 + 量比≥{cfg.volume_ratio_min} "
            f"+ 站稳5日线，排除≥{cfg.max_boards}连板」未筛到合适标的，"
            f"可能行情不活跃，建议空仓等待。"
        )

    @staticmethod
    def _assemble(picks: list[dict], verdicts: str, cfg: YangjiaConfig) -> str:
        """拼装报告：入选清单 + 快速建议 + 操作纪律"""
        sep = "\n\n" + "=" * 30 + "\n\n"

        header = [
            "🎯 养家选股（最笨的方法）",
            f"筛选：近{cfg.lookback_days}天涨停 + 量比≥{cfg.volume_ratio_min} + 站稳5日线，"
            f"排除≥{cfg.max_boards}连板\n",
            f"入选 {len(picks)} 只：",
        ]
        for i, p in enumerate(picks, 1):
            vr = p.get("vr")
            vr_text = f"量比{vr}" if vr is not None else "量比-"
            header.append(
                f"  {i}. {p['name']}（{p['code']}）"
                f" 现价{p['price']} {vr_text}"
                f" {p['boards']}连板"
            )

        parts = ["\n".join(header)]
        if verdicts:
            parts.append(sep + "💡 快速建议：\n" + verdicts)

        discipline = sep + "\n".join([
            "📏 操作纪律：",
            f"1. {cfg.advice_rule3}",
            f"2. {cfg.advice_rule4}",
            f"3. {cfg.advice_rule5}",
            "\n如需某只详细分析，回复「深度分析 代码」。",
            "以上仅供参考，不构成投资建议，投资有风险，入市需谨慎。",
        ])
        parts.append(discipline)
        return "\n".join(parts)

    # ── 中长线策略 ──

    async def _pick_midlong(self, wechat_id: str, count: int | None = None) -> str:
        """中长线选股主流程（均线趋势 + 基本面）"""
        cfg = await self.config_service.get_midlong_config()

        # 阶段A：候选池（中长线不打板，用活跃度做宽口径粗筛，量比阈值设0）
        pool = await self.akshare.get_active_pool(min_volume_ratio=0.0, cap=cfg.candidate_cap)
        if not pool:
            return "暂时无法获取行情数据，请稍后再试。"

        # 阶段B：技术面 + 基本面筛选
        winners = await self._screen_pool_midlong(pool, cfg)
        if not winners:
            return self._no_match_message_midlong(cfg)

        picks = self._select_new_picks(wechat_id, winners, count or cfg.output_count)

        verdicts = await self._quick_verdicts(picks, "中长线趋势+基本面")
        return self._assemble_midlong(picks, verdicts, cfg)

    async def _screen_pool_midlong(self, pool: list[dict], cfg: MidLongConfig) -> list[dict]:
        """逐只候选：先过技术面（便宜），通过再拉基本面校验"""
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
            tech_ok, _ = midlong_screener.screen_technical(tech, cfg)
            if not tech_ok:
                continue

            try:
                fundamentals = await self.akshare.get_fundamentals(code)
            except Exception as e:
                logger.warning("拉取 %s 基本面失败: %s", code, e)
                fundamentals = None

            result = midlong_screener.screen_midlong(tech, fundamentals, cfg)
            if result.passed:
                winners.append({
                    **candidate,
                    "fund": fundamentals,
                    "signal": self._tech_signal(tech),
                })
        return winners

    @staticmethod
    def _no_match_message_midlong(cfg: MidLongConfig) -> str:
        return (
            f"中长线选股：当前按「均线多头 + 趋势向上 + ROE≥{cfg.min_roe}% / "
            f"0<PE≤{cfg.max_pe} / 营收增长≥{cfg.min_revenue_growth}%」未筛到合适标的，"
            f"建议耐心等待趋势明朗。"
        )

    @staticmethod
    def _assemble_midlong(picks: list[dict], verdicts: str, cfg: MidLongConfig) -> str:
        """拼装中长线报告：入选清单 + 快速建议 + 操作纪律"""
        sep = "\n\n" + "=" * 30 + "\n\n"

        header = [
            "🎯 中长线选股（趋势 + 基本面）",
            f"筛选：均线多头 + 趋势向上 + ROE≥{cfg.min_roe}% / 0<PE≤{cfg.max_pe} / "
            f"营收增长≥{cfg.min_revenue_growth}%\n",
            f"入选 {len(picks)} 只：",
        ]
        for i, p in enumerate(picks, 1):
            header.append(
                f"  {i}. {p['name']}（{p['code']}）现价{p['price']}"
                f" 涨跌{p['change_pct']:+.2f}%{StockPickerService._fund_brief(p.get('fund'))}"
            )

        parts = ["\n".join(header)]
        if verdicts:
            parts.append(sep + "💡 快速建议：\n" + verdicts)

        discipline = sep + "\n".join([
            "📏 操作纪律：",
            f"1. {cfg.advice_hold}",
            f"2. {cfg.advice_stop}",
            f"3. {cfg.advice_add}",
            "\n如需某只详细分析，回复「深度分析 代码」。",
            "以上仅供参考，不构成投资建议，投资有风险，入市需谨慎。",
        ])
        parts.append(discipline)
        return "\n".join(parts)

    @staticmethod
    def _fund_brief(fund) -> str:
        """基本面简述（ROE/PE），无数据返回空串"""
        if not fund:
            return ""
        bits = []
        if fund.roe is not None:
            bits.append(f"ROE{fund.roe:.0f}%")
        if fund.pe_ratio is not None:
            bits.append(f"PE{fund.pe_ratio:.0f}")
        return " " + " ".join(bits) if bits else ""
