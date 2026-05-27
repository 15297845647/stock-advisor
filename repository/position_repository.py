"""持仓数据仓库 — 买入/卖出/查询/清仓"""

from datetime import date

from infrastructure.database import get_connection


class PositionRepository:

    async def open_position(
        self,
        wechat_id: str,
        stock_code: str,
        stock_name: str,
        shares: int,
        cost_price: float,
        open_date: str | None = None,
        direction: str = "long",
        note: str = "",
    ) -> int:
        """建仓 / 加仓，返回持仓 ID"""
        conn = await get_connection()
        try:
            cursor = await conn.execute(
                "INSERT INTO user_positions "
                "(wechat_id, stock_code, stock_name, direction, shares, cost_price, open_date, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (wechat_id, stock_code, stock_name, direction, shares, cost_price,
                 open_date or date.today().isoformat(), note),
            )
            await conn.commit()
            return cursor.lastrowid
        finally:
            await conn.close()

    async def close_position(
        self,
        position_id: int,
        close_price: float,
        close_date: str | None = None,
    ):
        """平仓（标记 status='closed'）"""
        conn = await get_connection()
        try:
            await conn.execute(
                "UPDATE user_positions SET status='closed', close_price=?, close_date=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (close_price, close_date or date.today().isoformat(), position_id),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def close_by_code(
        self,
        wechat_id: str,
        stock_code: str,
        close_price: float,
        close_date: str | None = None,
    ) -> int:
        """按股票代码平仓所有 open 持仓，返回平仓数"""
        conn = await get_connection()
        try:
            cursor = await conn.execute(
                "UPDATE user_positions SET status='closed', close_price=?, close_date=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE wechat_id=? AND stock_code=? AND status='open'",
                (close_price, close_date or date.today().isoformat(), wechat_id, stock_code),
            )
            await conn.commit()
            return cursor.rowcount
        finally:
            await conn.close()

    async def get_open_positions(self, wechat_id: str) -> list[dict]:
        """获取用户所有未平仓持仓"""
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT * FROM user_positions WHERE wechat_id=? AND status='open' ORDER BY open_date",
                (wechat_id,),
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    async def get_all_positions(self, wechat_id: str, limit: int = 50) -> list[dict]:
        """获取用户所有持仓（含已平仓）"""
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT * FROM user_positions WHERE wechat_id=? ORDER BY created_at DESC LIMIT ?",
                (wechat_id, limit),
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    async def get_all_users_with_positions(self) -> list[tuple[str, list[dict]]]:
        """获取所有有持仓的用户（用于定时策略推送）"""
        conn = await get_connection()
        try:
            users = await conn.execute_fetchall(
                "SELECT DISTINCT wechat_id FROM user_positions WHERE status='open'"
            )
            result = []
            for u in users:
                wid = u["wechat_id"]
                rows = await conn.execute_fetchall(
                    "SELECT * FROM user_positions WHERE wechat_id=? AND status='open' ORDER BY open_date",
                    (wid,),
                )
                positions = [dict(r) for r in rows]
                if positions:
                    result.append((wid, positions))
            return result
        finally:
            await conn.close()
