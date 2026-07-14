"""管理后台 FastAPI 应用 — 挂载 API + 静态文件 + 定时任务"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from admin.api import router as api_router
from infrastructure.database import init_db
from infrastructure.log_setup import cleanup_old_logs, rotate_plain_log

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


async def _daily_log_maintenance():
    """每天凌晨轮转纯文本日志 + 清理过期文件"""
    while True:
        await asyncio.sleep(3600)
        try:
            rotate_plain_log("cc-connect.log")
            rotate_plain_log("admin.log")
            cleanup_old_logs()
            logger.info("日志维护完成")
        except Exception as e:
            logger.warning("日志维护异常: %s", e)


def _start_scheduler() -> AsyncIOScheduler:
    """启动定时任务调度器（常驻 admin 进程，比 agent 子进程更可靠）"""
    from scheduler.daily_push import DailyPushScheduler
    from scheduler.news_sync import run_news_sync

    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    # 每交易日 07:30 拉自选股新闻
    scheduler.add_job(
        run_news_sync,
        CronTrigger(hour=7, minute=30, day_of_week="mon-fri"),
        id="news_sync",
        name="自选股新闻同步",
    )

    pusher = DailyPushScheduler()

    # 晨推：每日推荐（默认 09:00）
    scheduler.add_job(
        pusher.run_morning_recommend,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=0),
        id="morning_push",
        name="每日推荐推送",
        replace_existing=True,
    )

    # 午推：收盘分析（默认 15:30）
    scheduler.add_job(
        pusher.run_afternoon_analysis,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=30),
        id="afternoon_push",
        name="收盘分析推送",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("定时调度器已启动: 09:00 晨推, 15:30 午推, 07:30 新闻同步")

    asyncio.ensure_future(_apply_schedule_config(scheduler))

    return scheduler


async def _apply_schedule_config(scheduler: AsyncIOScheduler):
    """从 DB 加载推送配置，更新两个推送任务的调度时间"""
    try:
        from scheduler.daily_push import load_schedule_config
        cfg = await load_schedule_config()

        m_hour = cfg.get("morning_hour", 9)
        m_min = cfg.get("morning_minute", 0)
        scheduler.reschedule_job(
            "morning_push",
            trigger=CronTrigger(day_of_week="mon-fri", hour=m_hour, minute=m_min),
        )

        a_hour = cfg.get("afternoon_hour", 15)
        a_min = cfg.get("afternoon_minute", 30)
        scheduler.reschedule_job(
            "afternoon_push",
            trigger=CronTrigger(day_of_week="mon-fri", hour=a_hour, minute=a_min),
        )

        logger.info(
            "推送时间已从配置更新: 晨推 %02d:%02d, 午推 %02d:%02d",
            m_hour, m_min, a_hour, a_min,
        )
    except Exception as e:
        logger.warning("加载推送配置失败，使用默认时间: %s", e)


@asynccontextmanager
async def lifespan(application: FastAPI):
    await init_db()
    task = asyncio.create_task(_daily_log_maintenance())
    scheduler = _start_scheduler()
    application.state.scheduler = scheduler
    try:
        yield
    finally:
        task.cancel()
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass


app = FastAPI(title="Stock Advisor Admin", docs_url=None, redoc_url=None, lifespan=lifespan)
app.include_router(api_router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon")


@app.get("/r/{share_token}")
async def public_report(share_token: str):
    """公开分享报告页 — 无需登录，token 即凭证"""
    from application.report_export_service import ReportExportService
    from fastapi.responses import HTMLResponse
    svc = ReportExportService()
    html = await svc.render_html(share_token)
    if html is None:
        return HTMLResponse(
            content="<h2>报告已过期或不存在</h2><p>分享链接 7 天有效，请重新生成。</p>",
            status_code=404,
        )
    return HTMLResponse(content=html)
