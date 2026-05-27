"""订阅管理编排"""

from infrastructure.akshare_client import AKShareClient
from repository.user_repository import UserRepository


class SubscriptionService:
    def __init__(self):
        self.user_repo = UserRepository()
        self.akshare = AKShareClient()

    async def subscribe(self, wechat_id: str, stock_code: str) -> str:
        quote = await self.akshare.get_realtime_quote(stock_code)
        if not quote:
            return f"未找到股票 {stock_code}，请确认代码是否正确。"

        await self.user_repo.subscribe(wechat_id, stock_code, quote.name)
        return f"已关注 {quote.name}({stock_code})，每日收盘后会为你推送分析。"

    async def unsubscribe(self, wechat_id: str, stock_code: str) -> str:
        await self.user_repo.unsubscribe(wechat_id, stock_code)
        return f"已取消关注 {stock_code}。"

    async def show_watchlist(self, wechat_id: str) -> str:
        codes = await self.user_repo.get_watchlist(wechat_id)
        if not codes:
            return "你还没有关注任何股票。发送「关注 000001」即可添加。"
        lines = ["📋 你的关注列表："]
        for i, code in enumerate(codes, 1):
            lines.append(f"  {i}. {code}")
        lines.append("\n发送「取消关注 代码」可移除。")
        return "\n".join(lines)
