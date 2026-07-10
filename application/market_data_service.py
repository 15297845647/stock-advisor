"""市场数据服务 — 准备 LLM 所需的市场快照上下文

将全量行情缓存格式化为 prompt 友好的文本，供统一对话使用。
数据依赖 AKShareClient 的 60s 内存缓存，本层不重复缓存。
"""

import logging
import re

from domain.models.user_context import UserContext
from domain.stock_analyzer import analyze_technical
from infrastructure.akshare_client import AKShareClient

logger = logging.getLogger(__name__)

_RISK_MAP = {"conservative": "保守型", "moderate": "稳健型", "aggressive": "激进型"}
_STYLE_MAP = {"day": "短线/打板", "swing": "波段", "position": "中长线", "long": "中长线"}

_STOCK_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


class MarketDataService:
    def __init__(self):
        self.akshare = AKShareClient()

    async def build_market_context(self, ctx: UserContext, message: str = "") -> str:
        """组装用户画像 + 市场快照 + 个股数据，作为 LLM user message 的前缀上下文"""
        parts = []

        parts.append(self._format_user_profile(ctx))

        # 检测消息中的股票代码，拉取个股实时数据
        stock_codes = self._extract_stock_codes(message)
        if not stock_codes:
            # 尝试从名称识别
            stock_codes = await self.resolve_stock_names(message)

        if stock_codes:
            stock_data = await self._get_stock_details(stock_codes)
            if stock_data:
                parts.append(stock_data)
        else:
            # 没有明确个股时，注入市场快照供推荐
            market_snapshot = await self._get_market_snapshot()
            if market_snapshot:
                parts.append(market_snapshot)

        return "\n\n".join(parts)

    @staticmethod
    def _extract_stock_codes(message: str) -> list[str]:
        """从消息中提取6位股票代码"""
        codes = _STOCK_CODE_RE.findall(message)
        # 过滤明显不是股票代码的（如日期 202607）
        return [c for c in codes if c[0] in "0136"]

    async def resolve_stock_names(self, message: str) -> list[str]:
        """从消息中尝试识别股票名称并转为代码（用于无明确代码时）"""
        import re as _re
        name_patterns = _re.findall(r"(?:分析|看看|查一下|怎么样)\s*([^\d\s,，]{2,6})", message)
        if not name_patterns:
            return []

        codes = []
        for name in name_patterns[:3]:
            code = await self.akshare.resolve_stock_name(name)
            if code:
                codes.append(code)
        return codes

    async def _get_stock_details(self, codes: list[str]) -> str | None:
        """获取指定股票的详细实时数据（行情+技术指标+资金流向）"""
        sections = []

        for code in codes[:3]:  # 最多同时查 3 只
            try:
                detail = await self._fetch_single_stock(code)
                if detail:
                    sections.append(detail)
            except Exception as e:
                logger.warning("获取 %s 详情失败: %s", code, e)

        if not sections:
            return None

        return "【个股实时数据】\n\n" + "\n\n".join(sections)

    async def fetch_stocks_detail(self, codes: list[str]) -> str | None:
        """并行批量拉取多只股票的详细数据（推荐验证用）"""
        import asyncio

        tasks = [self._fetch_single_stock(code) for code in codes[:8]]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        sections = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning("批量拉取 %s 失败: %s", codes[i], result)
            elif result:
                sections.append(result)

        if not sections:
            return None

        return "\n\n".join(sections)

    async def _fetch_single_stock(self, code: str) -> str | None:
        """拉取单只股票的完整数据并格式化"""
        quote = await self.akshare.get_realtime_quote(code)
        if not quote:
            return None

        lines = [f"━━ {quote.name}（{code}）━━"]
        lines.append(f"现价: {quote.price}  涨跌: {quote.change_pct:+.2f}%")
        if quote.high and quote.low:
            lines.append(f"今开: {quote.open_price}  最高: {quote.high}  最低: {quote.low}")
        if quote.volume:
            vol_wan = quote.volume / 10000
            lines.append(f"成交量: {vol_wan:.0f}万手  成交额: {quote.amount / 1e8:.2f}亿" if quote.amount else f"成交量: {vol_wan:.0f}万手")

        # K线 + 技术指标
        bars = await self.akshare.get_stock_history(code, days=30)
        if bars:
            tech = analyze_technical(bars)
            if tech:
                lines.append(f"\n技术面:")
                lines.append(f"  趋势: {tech.trend}  MA5={tech.ma5} MA10={tech.ma10} MA20={tech.ma20}")
                lines.append(f"  MACD: DIF={tech.macd:.3f} DEA={tech.macd_signal:.3f} 柱={tech.macd_hist:+.3f}")
                lines.append(f"  RSI(14): {tech.rsi_14:.1f}  KDJ: K={tech.kdj_k:.1f}/D={tech.kdj_d:.1f}/J={tech.kdj_j:.1f}")
                lines.append(f"  支撑: {tech.support}  压力: {tech.resistance}")

            # 近5日K线
            recent = bars[-5:]
            lines.append(f"\n近5日K线:")
            for b in recent:
                lines.append(f"  {b.trade_date}: 开{b.open} 高{b.high} 低{b.low} 收{b.close} 涨跌{b.change_pct:+.2f}%")

        # 资金流向
        try:
            flows = await self.akshare.get_fund_flow(code)
            if flows:
                lines.append(f"\n资金流向(近3日):")
                for f in flows[:3]:
                    lines.append(f"  {f.trade_date}: 主力净流入{f.main_net_inflow / 1e4:.0f}万")
        except Exception:
            pass

        return "\n".join(lines)

    @staticmethod
    def _format_user_profile(ctx: UserContext) -> str:
        """格式化用户画像"""
        risk = _RISK_MAP.get(ctx.profile.risk_level, ctx.profile.risk_level)
        style = _STYLE_MAP.get(ctx.profile.trade_style, ctx.profile.trade_style)

        lines = [
            "【用户画像】",
            f"- 风险偏好：{risk}",
            f"- 交易风格：{style}",
        ]

        if ctx.watchlist:
            lines.append(f"- 自选股：{', '.join(ctx.watchlist)}")

        if ctx.memories:
            lines.append("- 记忆：" + "；".join(ctx.memories[:5]))

        return "\n".join(lines)

    async def _get_market_snapshot(self) -> str | None:
        """获取市场快照：Top50 活跃股 + 基本指标"""
        try:
            pool = await self.akshare.get_active_pool(min_volume_ratio=0.0, cap=50)
        except Exception as e:
            logger.warning("获取市场快照失败: %s", e)
            return None

        if not pool:
            return None

        lines = ["【实时市场数据】（今日活跃股 Top50，按涨幅排序）"]
        lines.append("代码 | 名称 | 现价 | 涨跌% | 量比 | 换手%")
        lines.append("-" * 50)

        for s in pool:
            vr = f"{s['volume_ratio']:.1f}" if s.get("volume_ratio") else "-"
            tr = f"{s['turnover']:.1f}" if s.get("turnover") else "-"
            lines.append(
                f"{s['code']} | {s['name']} | {s['price']} | "
                f"{s['change_pct']:+.2f}% | {vr} | {tr}"
            )

        return "\n".join(lines)
