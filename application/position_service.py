"""持仓管理编排 — 录入、平仓、查询、盈亏计算"""

import logging

from infrastructure.akshare_client import AKShareClient
from repository.position_repository import PositionRepository

logger = logging.getLogger(__name__)


class PositionService:
    def __init__(self):
        self.repo = PositionRepository()
        self.akshare = AKShareClient()

    async def add_position(
        self,
        wechat_id: str,
        stock_code: str,
        shares: int | None,
        cost_price: float | None,
    ) -> str:
        """用户通过对话录入持仓"""
        if not stock_code:
            return "请提供股票代码，如「买入 600519 100股 成本180」。"

        # 验证股票代码
        quote = await self.akshare.get_realtime_quote(stock_code)
        if not quote:
            return f"未找到股票 {stock_code}，请检查代码。"

        stock_name = quote.name if quote else ""

        if not shares:
            shares = 100

        if not cost_price:
            cost_price = quote.price if quote else 0.0

        pid = await self.repo.open_position(
            wechat_id=wechat_id,
            stock_code=stock_code,
            stock_name=stock_name,
            shares=shares,
            cost_price=cost_price,
        )

        return (
            f"已录入持仓 ✅\n"
            f"股票：{stock_name}（{stock_code}）\n"
            f"数量：{shares}股\n"
            f"成本价：{cost_price:.2f}\n"
            f"当前价：{quote.price:.2f}\n"
            f"回复「持仓」查看全部仓位"
        )

    async def close_position(
        self,
        wechat_id: str,
        stock_code: str,
        sell_price: float | None,
    ) -> str:
        """用户通过对话卖出/平仓"""
        if not stock_code:
            return "请提供股票代码，如「卖出 600519」或「卖出 600519 185元」。"

        # 未提供价格则取实时价
        if not sell_price:
            quote = await self.akshare.get_realtime_quote(stock_code)
            sell_price = quote.price if quote else 0.0

        count = await self.repo.close_by_code(wechat_id, stock_code, sell_price)
        if count == 0:
            return f"未找到 {stock_code} 的持仓记录。"

        return f"已平仓 {stock_code}，卖出价 {sell_price:.2f}，共 {count} 笔持仓已关闭 ✅"

    async def show_positions(self, wechat_id: str) -> str:
        """展示用户当前持仓 + 实时盈亏"""
        positions = await self.repo.get_open_positions(wechat_id)
        if not positions:
            return "当前没有持仓记录。\n回复「买入 600519 100股 成本180」录入持仓。"

        lines = ["📊 当前持仓\n"]
        total_cost = 0.0
        total_market = 0.0

        for p in positions:
            code = p["stock_code"]
            quote = await self.akshare.get_realtime_quote(code)
            current_price = quote.price if quote else p["cost_price"]

            cost_val = p["shares"] * p["cost_price"]
            market_val = p["shares"] * current_price
            pnl = market_val - cost_val
            pnl_pct = (pnl / cost_val * 100) if cost_val > 0 else 0

            total_cost += cost_val
            total_market += market_val

            emoji = "🔴" if pnl < 0 else "🟢"
            lines.append(
                f"{emoji} {p['stock_name']}（{code}）\n"
                f"   {p['shares']}股 | 成本{p['cost_price']:.2f} | "
                f"现价{current_price:.2f} | {pnl:+,.0f}元（{pnl_pct:+.1f}%）"
            )

        total_pnl = total_market - total_cost
        total_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
        lines.append(
            f"\n💰 总成本 {total_cost:,.0f} | 市值 {total_market:,.0f} | "
            f"盈亏 {total_pnl:+,.0f}（{total_pct:+.1f}%）"
        )

        return "\n".join(lines)

    async def build_position_summary(self, positions: list[dict]) -> str:
        """为策略推送构建持仓摘要文本（含实时行情）"""
        lines = []
        for p in positions:
            code = p["stock_code"]
            quote = await self.akshare.get_realtime_quote(code)
            current_price = quote.price if quote else p["cost_price"]
            cost_val = p["shares"] * p["cost_price"]
            market_val = p["shares"] * current_price
            pnl_pct = ((current_price - p["cost_price"]) / p["cost_price"] * 100) if p["cost_price"] > 0 else 0

            lines.append(
                f"- {p['stock_name']}（{code}）：{p['shares']}股, "
                f"成本{p['cost_price']:.2f}, 现价{current_price:.2f}, "
                f"盈亏{pnl_pct:+.1f}%, 持仓市值{market_val:,.0f}元"
            )

        return "\n".join(lines)
