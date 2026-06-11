"""策略配置持久化 — 以 JSON 形式存于 strategy_config 表

单条记录（key='yangjia'）存养家策略全部参数，便于整体读写。
"""

import json
import logging

from infrastructure.database import get_connection

logger = logging.getLogger(__name__)

_YANGJIA_KEY = "yangjia"


class StrategyConfigRepository:
    async def load(self) -> dict:
        """读取养家策略配置 JSON，无记录时返回空字典"""
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT value FROM strategy_config WHERE key = ?", (_YANGJIA_KEY,)
            )
            if not rows:
                return {}
            return json.loads(rows[0]["value"])
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("策略配置解析失败，回退默认: %s", e)
            return {}
        finally:
            await conn.close()

    async def save(self, config: dict) -> None:
        """整体覆盖写入养家策略配置"""
        payload = json.dumps(config, ensure_ascii=False)
        conn = await get_connection()
        try:
            await conn.execute(
                "INSERT INTO strategy_config (key, value, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = CURRENT_TIMESTAMP",
                (_YANGJIA_KEY, payload),
            )
            await conn.commit()
        finally:
            await conn.close()
