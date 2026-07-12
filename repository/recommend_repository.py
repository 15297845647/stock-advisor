"""推荐记录 Repository — 落库供回测

单一职责：recommendations 表 CRUD。
"""

import json
import logging
from datetime import datetime, timedelta

from domain.models.recommendation import Recommendation
from infrastructure.database import get_connection

logger = logging.getLogger(__name__)


class RecommendRepository:
    """推荐记录 CRUD"""

    async def insert_batch(
        self,
        wechat_id: str | None,
        recommendations: list[Recommendation],
        intent_dict: dict,
    ) -> int:
        """批量插入本次推荐；返回插入条数"""
        if not recommendations:
            return 0

        intent_json = json.dumps(intent_dict, ensure_ascii=False)
        conn = await get_connection()
        try:
            for rec in recommendations:
                await conn.execute(
                    "INSERT INTO recommendations "
                    "(wechat_id, stock_code, stock_name, recommended_at, "
                    " recommend_price, target_price, stop_loss, risk_score, "
                    " reason, intent_json, adjusted) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        wechat_id, rec.code, rec.name,
                        rec.recommended_at.isoformat(),
                        rec.price, rec.target_price, rec.stop_loss,
                        rec.risk_score, rec.reason, intent_json,
                        1 if rec.adjusted else 0,
                    ),
                )
            await conn.commit()
            return len(recommendations)
        finally:
            await conn.close()

    async def recent_codes(
        self, wechat_id: str, hours: int = 24,
    ) -> list[str]:
        """查用户近 N 小时被推荐过的代码（供"再推一批"去重）"""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT DISTINCT stock_code FROM recommendations "
                "WHERE wechat_id = ? AND recommended_at >= ?",
                (wechat_id, cutoff),
            )
            return [r["stock_code"] for r in rows]
        finally:
            await conn.close()

    async def get_user_history(
        self, wechat_id: str, limit: int = 50,
    ) -> list[dict]:
        """查用户的推荐历史（供 Admin 查看）"""
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT stock_code, stock_name, recommended_at, "
                "recommend_price, target_price, stop_loss, risk_score, reason, "
                "adjusted, outcome, outcome_price "
                "FROM recommendations WHERE wechat_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (wechat_id, limit),
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()
