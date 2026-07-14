"""每日定时推送 — 双推送任务

晨推（默认 09:00）：为每个用户推荐股票 + 操作建议
午推（默认 15:30）：为每个用户推送收盘行情分析 + 操作建议

嵌入 admin 常驻进程，通过 APScheduler 触发。
推送时间从 DB 配置读取，后台可修改。
"""

import json
import logging
import subprocess
from datetime import date

from application.analysis_service import AnalysisService
from application.recommend_service import RecommendService
from domain.models.user_context import UserContext, UserProfile
from infrastructure.akshare_client import AKShareClient
from infrastructure.database import get_connection

logger = logging.getLogger(__name__)

SCHEDULE_CONFIG_KEY = "schedule"

DEFAULT_SCHEDULE = {
    "morning_enabled": True,
    "morning_hour": 9,
    "morning_minute": 0,
    "afternoon_enabled": True,
    "afternoon_hour": 15,
    "afternoon_minute": 30,
}


class DailyPushScheduler:
    def __init__(self):
        self.analysis = AnalysisService()
        self.recommend = RecommendService()
        self.akshare = AKShareClient()

    # ────────────── 晨推：每日推荐股票 ──────────────

    async def run_morning_recommend(self):
        """交易日开盘前：为每个用户推荐股票 + 操作建议"""
        cfg = await load_schedule_config()
        if not cfg.get("morning_enabled", True):
            logger.info("[MorningPush] 晨推已关闭，跳过")
            return

        if not await self.akshare.is_trade_day(date.today()):
            logger.info("[MorningPush] 非交易日，跳过")
            return

        logger.info("[MorningPush] 开始每日推荐推送...")
        await self._push_recommendations()
        logger.info("[MorningPush] 推荐推送完成")

    async def _push_recommendations(self) -> int:
        """遍历开启晨推的用户，为每人生成推荐并推送。"""
        users = await self._get_push_users("morning_push")
        if not users:
            logger.warning("[MorningPush] 无用户开启晨推")
            return 0
        for wechat_id, profile in users:
            await self._recommend_and_push(wechat_id, profile)
        logger.info("[MorningPush] 推荐完成，共 %d 个用户", len(users))
        return len(users)

    async def _recommend_and_push(self, wechat_id: str, profile: UserProfile):
        """为单个用户生成推荐并推送"""
        try:
            ctx = UserContext(profile=profile)
            recs, summary = await self.recommend.recommend(
                "推荐几只今日值得关注的股票", ctx,
            )
            message = RecommendService.format_response(recs, summary)
            text = f"📈 {date.today()} 每日推荐\n\n{message}"
            bot_name = f"bot-{wechat_id}"
            _send_via_cc_connect(bot_name, text)
        except Exception as e:
            logger.error("[MorningPush] 用户 %s 推荐失败: %s", wechat_id, e)

    async def _get_push_users(self, push_type: str) -> list[tuple[str, UserProfile]]:
        """获取开启了指定推送的用户。push_type: morning_push 或 afternoon_push"""
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                f"SELECT wechat_id, nickname, risk_level, trade_style "
                f"FROM users WHERE {push_type} = 1",
            )
            return [
                (
                    r["wechat_id"],
                    UserProfile(
                        wechat_id=r["wechat_id"],
                        nickname=r["nickname"] or "",
                        risk_level=r["risk_level"] or "moderate",
                        trade_style=r["trade_style"] or "swing",
                    ),
                )
                for r in rows
            ]
        finally:
            await conn.close()

    # ────────────── 午推：大盘行情分析 ──────────────

    async def run_afternoon_analysis(self):
        """交易日收盘后：推送大盘行情分析 + 操作建议"""
        cfg = await load_schedule_config()
        if not cfg.get("afternoon_enabled", True):
            logger.info("[AfternoonPush] 午推已关闭，跳过")
            return

        if not await self.akshare.is_trade_day(date.today()):
            logger.info("[AfternoonPush] 非交易日，跳过")
            return

        logger.info("[AfternoonPush] 开始大盘分析推送...")
        await self._push_market_analysis()
        logger.info("[AfternoonPush] 推送完成")

    async def _push_market_analysis(self) -> int:
        """生成大盘行情分析，推送给所有用户。返回实际推送用户数。"""
        overview = await self.analysis.get_market_overview()
        if not overview:
            raise RuntimeError("大盘分析无输出")

        message = f"📊 {date.today()} 收盘行情分析\n\n{overview}"
        message += "\n\n回复股票代码可查看个股详细分析。"

        users = await self._get_push_users("afternoon_push")
        if not users:
            logger.warning("[AfternoonPush] 无用户开启午推")
            return 0

        for wechat_id, _ in users:
            bot_name = f"bot-{wechat_id}"
            _send_via_cc_connect(bot_name, message)
        logger.info("[AfternoonPush] 大盘分析已推送 %d 个用户", len(users))
        return len(users)


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
