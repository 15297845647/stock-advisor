"""管理后台 FastAPI 应用 — 挂载 API + 静态文件"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

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


@asynccontextmanager
async def lifespan(application: FastAPI):
    await init_db()
    task = asyncio.create_task(_daily_log_maintenance())
    yield
    task.cancel()


app = FastAPI(title="Stock Advisor Admin", docs_url=None, redoc_url=None, lifespan=lifespan)
app.include_router(api_router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon")
