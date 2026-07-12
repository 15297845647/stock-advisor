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
    """启动定时任务调度器"""
    from scheduler.news_sync import run_news_sync

    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    # 每交易日 07:30 拉自选股新闻
    scheduler.add_job(
        run_news_sync,
        CronTrigger(hour=7, minute=30, day_of_week="mon-fri"),
        id="news_sync",
        name="自选股新闻同步",
    )

    scheduler.start()
    logger.info("定时调度器已启动: 07:30 自选股新闻同步")
    return scheduler


@asynccontextmanager
async def lifespan(application: FastAPI):
    await init_db()
    task = asyncio.create_task(_daily_log_maintenance())
    scheduler = _start_scheduler()
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
