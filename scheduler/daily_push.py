"""每日定时推送 — 收盘后自选股分析

嵌入 admin 常驻进程，通过 APScheduler 触发。
推送时间从 DB 配置读取（默认 15:30），后台可修改。
"""

import json
import logging
import subprocess
from datetime import date

from application.analysis_service import AnalysisService
from infrastructure.akshare_client import AKShareClient
from infrastructure.database import get_connection
from repository.user_repository import UserRepository

logger = logging.getLogger(__name__)

SCHEDULE_CONFIG_KEY = "schedule"

DEFAULT_SCHEDULE = {
    "push_enabled": True,
    "push_hour": 15,
    "push_minute": 30,
}


class DailyPushScheduler:
    def __init__(self):
        self.analysis = AnalysisService()
        self.user_repo = UserRepository()
        self.akshare = AKShareClient()

    async def run_daily_analysis(self):
        """交易日收盘后：给每个有自选股的用户推送分析"""
        cfg = await load_schedule_config()
        if not cfg.get("push_enabled", True):
            logger.info("[DailyPush] 推送已关闭，跳过")
            return

        if not await self.akshare.is_trade_day(date.today()):
            logger.info("[DailyPush] 非交易日，跳过")
            return

        logger.info("[DailyPush] 开始收盘后推送...")
        await self._push_watchlist_analysis()
        logger.info("[DailyPush] 推送完成")

    async def _push_watchlist_analysis(self):
        """遍历所有有自选股的用户，逐个分析推送"""
        users = await self.user_repo.get_all_users_with_watchlist()
        for wechat_id, stock_codes in users:
            await self._analyze_and_push(wechat_id, stock_codes)
        logger.info("[DailyPush] 自选分析完成，共 %d 个用户", len(users))

    async def _analyze_and_push(self, wechat_id: str, stock_codes: list[str]):
        """分析用户自选股并推送结果"""
        summaries = []
        for code in stock_codes:
            try:
                report = await self.analysis.analyze_stock(code, force=True)
                summary = report[:200] + "..." if len(report) > 200 else report
                summaries.append(f"【{code}】\n{summary}")
            except Exception as e:
                logger.error("[DailyPush] 分析 %s 失败: %s", code, e)
                summaries.append(f"【{code}】分析失败")

        if not summaries:
            return

        message = f"📊 {date.today()} 收盘分析\n\n" + "\n\n".join(summaries)
        message += "\n\n回复股票代码可查看详细分析。"

        bot_name = f"bot-{wechat_id}"
        _send_via_cc_connect(bot_name, message)


def _send_via_cc_connect(project_name: str, text: str):
    """通过 cc-connect CLI 向指定 bot 发送消息"""
    try:
        result = subprocess.run(
            ["cc-connect", "send", "--project", project_name, "--text", text],
            timeout=30,
            capture_output=True,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")[:200]
            logger.warning(
                "[DailyPush] cc-connect send 失败 project=%s code=%d: %s",
                project_name, result.returncode, stderr,
            )
    except FileNotFoundError:
        logger.warning("[DailyPush] cc-connect CLI 未安装，无法推送")
    except subprocess.TimeoutExpired:
        logger.warning("[DailyPush] cc-connect send 超时 project=%s", project_name)
    except Exception as e:
        logger.error("[DailyPush] 推送失败 project=%s: %s", project_name, e)


# ────────────── 配置读写 ──────────────


async def load_schedule_config() -> dict:
    """从 strategy_config 表读推送配置，不存在则返回默认"""
    conn = await get_connection()
    try:
        rows = await conn.execute_fetchall(
            "SELECT value FROM strategy_config WHERE key = ?",
            (SCHEDULE_CONFIG_KEY,),
        )
        if not rows:
            return DEFAULT_SCHEDULE.copy()
        cfg = json.loads(rows[0]["value"])
        merged = {**DEFAULT_SCHEDULE, **cfg}
        return merged
    except Exception as e:
        logger.warning("[DailyPush] 读配置失败，用默认值: %s", e)
        return DEFAULT_SCHEDULE.copy()
    finally:
        await conn.close()


async def save_schedule_config(data: dict) -> dict:
    """写入推送配置到 strategy_config 表"""
    current = await load_schedule_config()
    current.update(data)

    payload = json.dumps(current, ensure_ascii=False)
    conn = await get_connection()
    try:
        await conn.execute(
            "INSERT INTO strategy_config (key, value, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = CURRENT_TIMESTAMP",
            (SCHEDULE_CONFIG_KEY, payload),
        )
        await conn.commit()
    finally:
        await conn.close()

    return current
