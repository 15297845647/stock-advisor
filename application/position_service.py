"""持仓管理编排 — LLM 语境标记 → 确认 → 录入 / 平仓 / 查询 / 盈亏"""

import logging

from infrastructure.akshare_client import AKShareClient
from repository.position_repository import PositionRepository

logger = logging.getLogger(__name__)

# 用户 pending 持仓（内存态，确认后写库）
_pending: dict[str, list[dict]] = {}


class PositionService:
    def __init__(self):
        self.repo = PositionRepository()
        self.akshare = AKShareClient()

    # ── 从 LLM 标记存入 pending ──

    async def store_pending_from_llm(self, wechat_id: str, llm_data: dict) -> str | None:
        """从 MiniMax 响应中的 [POSITION] 数据验证并存入 pending，返回确认文本"""
        positions = llm_data.get("positions", [])
        if not positions:
            return None

        validated = []
        for p in positions:
            code = p.get("stock_code")
            if not code or len(str(code)) != 6:
                continue

            # 数量和成本都没有 → 信息不足，跳过
            if not p.get("shares") and not p.get("cost_price"):
                logger.info("LLM 标记 %s 但数量和成本均为空，跳过", code)
                continue

            quote = await self.akshare.get_realtime_quote(str(code))
            if quote:
                validated.append({
                    "stock_code": str(code),
                    "stock_name": quote.name,
                    "shares": p.get("shares"),
                    "cost_price": p.get("cost_price"),
                    "current_price": quote.price,
                })
            else:
                logger.warning("LLM 标记的股票代码 %s 无效，跳过", code)

        if not validated:
            return None

        _pending[wechat_id] = validated
        return self._build_confirm_message(validated)

    def _build_confirm_message(self, positions: list[dict]) -> str:
        lines = ["📋 检测到以下持仓信息：\n"]

        for i, p in enumerate(positions, 1):
            shares_text = f"{p['shares']}股" if p.get("shares") else "数量未提供"
            cost_text = f"成本{p['cost_price']:.2f}" if p.get("cost_price") else "成本未提供"
            current = p.get("current_price")
            current_text = f"（现价{current:.2f}）" if current else ""

            lines.append(
                f"{i}. {p['stock_name']}（{p['stock_code']}）"
                f" {shares_text} {cost_text}{current_text}"
            )

        missing = []
        for p in positions:
            if not p.get("shares"):
                missing.append(f"{p['stock_name']}的数量")
            if not p.get("cost_price"):
                missing.append(f"{p['stock_name']}的成本价")

        if missing:
            lines.append(f"\n⚠️ 缺少：{'、'.join(missing)}")
            lines.append("请补充后重新告诉我，或回复「确认」先录入已有信息。")
        else:
            lines.append("\n回复「确认」录入持仓，回复「取消」放弃。")

        return "\n".join(lines)

    # ── 确认 / 取消 ──

    async def confirm_pending(self, wechat_id: str) -> str | None:
        positions = _pending.pop(wechat_id, None)
        if not positions:
            return None

        saved = []
        for p in positions:
            shares = p.get("shares") or 100
            cost = p.get("cost_price") or p.get("current_price", 0.0)

            await self.repo.open_position(
                wechat_id=wechat_id,
                stock_code=p["stock_code"],
                stock_name=p.get("stock_name", ""),
                shares=shares,
                cost_price=cost,
            )
            saved.append(f"  ✅ {p.get('stock_name', '')}（{p['stock_code']}）{shares}股 成本{cost:.2f}")

        lines = ["持仓录入成功！\n"] + saved + ["\n回复「持仓」查看全部仓位。"]
        return "\n".join(lines)

    def cancel_pending(self, wechat_id: str) -> str | None:
        if _pending.pop(wechat_id, None):
            return "已取消录入。"
        return None

    def has_pending(self, wechat_id: str) -> bool:
        return wechat_id in _pending

    # ── 平仓 / 查询 ──

    async def close_position(
        self, wechat_id: str, stock_code: str, sell_price: float | None,
    ) -> str:
        if not stock_code:
            return "请提供股票代码，如「卖出 600519」或「卖出 600519 185元」。"

        if not sell_price:
            quote = await self.akshare.get_realtime_quote(stock_code)
            sell_price = quote.price if quote else 0.0

        count = await self.repo.close_by_code(wechat_id, stock_code, sell_price)
        if count == 0:
            return f"未找到 {stock_code} 的持仓记录。"

        return f"已平仓 {stock_code}，卖出价 {sell_price:.2f}，共 {count} 笔持仓已关闭 ✅"

    async def show_positions(self, wechat_id: str) -> str:
        positions = await self.repo.get_open_positions(wechat_id)
        if not positions:
            return "当前没有持仓记录。\n告诉我你的持仓，如「我有茅台500股，成本1800」即可录入。"

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
        lines = []
        for p in positions:
            code = p["stock_code"]
            quote = await self.akshare.get_realtime_quote(code)
            current_price = quote.price if quote else p["cost_price"]
            pnl_pct = ((current_price - p["cost_price"]) / p["cost_price"] * 100) if p["cost_price"] > 0 else 0
            market_val = p["shares"] * current_price

            lines.append(
                f"- {p['stock_name']}（{code}）：{p['shares']}股, "
                f"成本{p['cost_price']:.2f}, 现价{current_price:.2f}, "
                f"盈亏{pnl_pct:+.1f}%, 持仓市值{market_val:,.0f}元"
            )

        return "\n".join(lines)
