"""每日定时推送 — 收盘后自选股分析

嵌入 agent 主进程，通过 APScheduler 触发：
- 15:30  收盘后自选分析推送
"""

import logging
import subprocess
from datetime import date

from application.analysis_service import AnalysisService
from infrastructure.akshare_client import AKShareClient
from repository.user_repository import UserRepository

logger = logging.getLogger(__name__)


class DailyPushScheduler:
    def __init__(self):
        self.analysis = AnalysisService()
        self.user_repo = UserRepository()
        self.akshare = AKShareClient()

    # ── 收盘后 15:30 ──

    async def run_daily_analysis(self):
        """交易日收盘后：自选股分析"""
        if not await self.akshare.is_trade_day(date.today()):
            logger.info("非交易日，跳过每日分析")
            return

        logger.info("开始收盘后推送...")
        await self._push_watchlist_analysis()
        logger.info("收盘后推送完成")

    async def _push_watchlist_analysis(self):
        """自选股收盘分析摘要"""
        users = await self.user_repo.get_all_users_with_watchlist()
        for wechat_id, stock_codes in users:
            await self._analyze_and_push(wechat_id, stock_codes)
        logger.info("自选分析完成，共 %d 个用户", len(users))

    async def _analyze_and_push(self, wechat_id: str, stock_codes: list[str]):
        summaries = []
        for code in stock_codes:
            try:
                report = await self.analysis.analyze_stock(code, force=True)
                summary = report[:200] + "..." if len(report) > 200 else report
                summaries.append(f"【{code}】\n{summary}")
            except Exception as e:
                logger.error("分析 %s 失败: %s", code, e)
                summaries.append(f"【{code}】分析失败")

        if not summaries:
            return

        message = f"📊 {date.today()} 收盘分析\n\n" + "\n\n".join(summaries)
        message += "\n\n回复股票代码可查看详细分析。"
        self._send_via_cc_connect(message)

    @staticmethod
    def _send_via_cc_connect(text: str):
        """通过 cc-connect CLI 发送消息"""
        try:
            subprocess.run(
                ["cc-connect", "send", "--text", text],
                timeout=30,
                capture_output=True,
            )
        except FileNotFoundError:
            logger.warning("cc-connect CLI 未安装，无法推送消息")
        except subprocess.TimeoutExpired:
            logger.warning("cc-connect send 超时")
        except Exception as e:
            logger.error("推送失败: %s", e)


def setup_scheduler() -> None:
    """配置并启动 APScheduler"""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = AsyncIOScheduler()
    pusher = DailyPushScheduler()

    # 收盘后 15:30 — 自选分析
    scheduler.add_job(
        pusher.run_daily_analysis,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=30),
        id="daily_analysis",
        name="收盘分析推送",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("定时任务已启动：收盘 15:30 自选分析")
