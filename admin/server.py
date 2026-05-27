"""管理后台 FastAPI 应用 — 挂载 API + 静态文件"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from admin.api import router as api_router
from infrastructure.database import init_db

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(application: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Stock Advisor Admin", docs_url=None, redoc_url=None, lifespan=lifespan)
app.include_router(api_router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon")
