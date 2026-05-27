"""后台管理 API 路由"""

import os
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from admin.auth import login, verify_token
from agent.config import (
    ADMIN_PASSWORD,
    DB_PATH,
    ENV_FILE_PATH,
    MINIMAX_API_KEY,
    MINIMAX_BASE_URL,
    MINIMAX_MODEL,
)
from infrastructure.database import get_connection

router = APIRouter(prefix="/api")


# ────────────────────── Auth ──────────────────────


class LoginRequest(BaseModel):
    password: str


@router.post("/login")
async def api_login(req: LoginRequest, response: Response):
    token = login(req.password)
    response.set_cookie("admin_token", token, httponly=True, max_age=86400)
    return {"token": token}


# ────────────────────── Dashboard ──────────────────────


@router.get("/dashboard", dependencies=[Depends(verify_token)])
async def dashboard():
    conn = await get_connection()
    try:
        user_count = (await conn.execute_fetchall("SELECT COUNT(*) as c FROM users"))[0]["c"]
        watch_count = (await conn.execute_fetchall("SELECT COUNT(*) as c FROM user_watchlist"))[0]["c"]
        report_count = (await conn.execute_fetchall("SELECT COUNT(*) as c FROM analysis_reports"))[0]["c"]
        chat_count = (await conn.execute_fetchall("SELECT COUNT(*) as c FROM chat_history"))[0]["c"]

        today = date.today().isoformat()
        today_reports = (await conn.execute_fetchall(
            "SELECT COUNT(*) as c FROM analysis_reports WHERE report_date = ?", (today,)
        ))[0]["c"]
        today_chats = (await conn.execute_fetchall(
            "SELECT COUNT(*) as c FROM chat_history WHERE date(created_at) = ?", (today,)
        ))[0]["c"]

        last_activity = await conn.execute_fetchall(
            "SELECT created_at FROM chat_history ORDER BY id DESC LIMIT 1"
        )
        last_active = last_activity[0]["created_at"] if last_activity else None

        db_size_bytes = Path(DB_PATH).stat().st_size if Path(DB_PATH).exists() else 0
    finally:
        await conn.close()

    return {
        "users": user_count,
        "watchlist_items": watch_count,
        "total_reports": report_count,
        "total_chats": chat_count,
        "today_reports": today_reports,
        "today_chats": today_chats,
        "last_activity": last_active,
        "db_size_mb": round(db_size_bytes / 1048576, 2),
        "minimax_model": MINIMAX_MODEL,
        "minimax_key_set": bool(MINIMAX_API_KEY),
    }


# ────────────────────── 用户管理 ──────────────────────


@router.get("/users", dependencies=[Depends(verify_token)])
async def list_users():
    conn = await get_connection()
    try:
        rows = await conn.execute_fetchall(
            "SELECT u.*, COUNT(w.stock_code) as watch_count "
            "FROM users u LEFT JOIN user_watchlist w ON u.wechat_id = w.wechat_id "
            "GROUP BY u.wechat_id ORDER BY u.created_at DESC"
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


@router.get("/users/{wechat_id}", dependencies=[Depends(verify_token)])
async def get_user_detail(wechat_id: str):
    conn = await get_connection()
    try:
        user_rows = await conn.execute_fetchall("SELECT * FROM users WHERE wechat_id = ?", (wechat_id,))
        if not user_rows:
            raise HTTPException(404, "用户不存在")

        watchlist = await conn.execute_fetchall(
            "SELECT stock_code, stock_name, added_at FROM user_watchlist WHERE wechat_id = ?", (wechat_id,)
        )
        memories = await conn.execute_fetchall(
            "SELECT content, category, created_at FROM user_memory WHERE wechat_id = ? ORDER BY created_at DESC LIMIT 30",
            (wechat_id,),
        )
        recent_chat = await conn.execute_fetchall(
            "SELECT role, content, created_at FROM chat_history WHERE wechat_id = ? ORDER BY id DESC LIMIT 20",
            (wechat_id,),
        )
        return {
            "profile": dict(user_rows[0]),
            "watchlist": [dict(r) for r in watchlist],
            "memories": [dict(r) for r in memories],
            "recent_chat": [dict(r) for r in reversed(list(recent_chat))],
        }
    finally:
        await conn.close()


class UpdateProfileRequest(BaseModel):
    nickname: str | None = None
    risk_level: str | None = None
    trade_style: str | None = None


@router.put("/users/{wechat_id}", dependencies=[Depends(verify_token)])
async def update_user(wechat_id: str, req: UpdateProfileRequest):
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        return {"ok": True}

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [wechat_id]

    conn = await get_connection()
    try:
        await conn.execute(f"UPDATE users SET {set_clause} WHERE wechat_id = ?", values)
        await conn.commit()
    finally:
        await conn.close()
    return {"ok": True}


# ────────────────────── Bot 管理 ──────────────────────


class AddBotRequest(BaseModel):
    user_id: str
    token: str = ""
    account_id: str = ""


class UpdateBotTokenRequest(BaseModel):
    token: str
    account_id: str


@router.get("/bots", dependencies=[Depends(verify_token)])
async def api_list_bots():
    from admin.bot_manager import list_bots
    return await list_bots()


@router.post("/bots", dependencies=[Depends(verify_token)])
async def api_add_bot(req: AddBotRequest):
    from admin.bot_manager import add_bot
    if not req.user_id.strip():
        raise HTTPException(400, "用户ID不能为空")
    try:
        bot = await add_bot(
            req.user_id.strip(),
            token=req.token.strip(),
            account_id=req.account_id.strip(),
        )
    except Exception as e:
        raise HTTPException(400, f"添加失败: {e}")
    return bot


@router.delete("/bots/{name}", dependencies=[Depends(verify_token)])
async def api_delete_bot(name: str):
    from admin.bot_manager import delete_bot
    await delete_bot(name)
    return {"ok": True}


@router.put("/bots/{name}/token", dependencies=[Depends(verify_token)])
async def api_update_bot_token(name: str, req: UpdateBotTokenRequest):
    from admin.bot_manager import update_bot_token
    await update_bot_token(name, req.token, req.account_id)
    return {"ok": True}


@router.post("/bots/restart", dependencies=[Depends(verify_token)])
async def api_restart_cc():
    from admin.bot_manager import restart_cc_connect
    msg = restart_cc_connect()
    return {"message": msg}


# ────────────────────── 配置管理 ──────────────────────


@router.get("/config", dependencies=[Depends(verify_token)])
async def get_config():
    """读取当前配置（脱敏）"""
    return {
        "minimax_api_key": _mask_key(MINIMAX_API_KEY),
        "minimax_base_url": MINIMAX_BASE_URL,
        "minimax_model": MINIMAX_MODEL,
        "db_path": DB_PATH,
        "admin_port": int(os.getenv("ADMIN_PORT", "8900")),
    }


class UpdateConfigRequest(BaseModel):
    minimax_api_key: str | None = None
    minimax_base_url: str | None = None
    minimax_model: str | None = None
    admin_password: str | None = None


@router.put("/config", dependencies=[Depends(verify_token)])
async def update_config(req: UpdateConfigRequest):
    """修改 .env 文件中的配置项（需重启生效）"""
    env_path = ENV_FILE_PATH
    env_map = _read_env_file(env_path)

    if req.minimax_api_key:
        env_map["MINIMAX_API_KEY"] = req.minimax_api_key
    if req.minimax_base_url:
        env_map["MINIMAX_BASE_URL"] = req.minimax_base_url
    if req.minimax_model:
        env_map["MINIMAX_MODEL"] = req.minimax_model
    if req.admin_password:
        env_map["ADMIN_PASSWORD"] = req.admin_password

    _write_env_file(env_path, env_map)
    return {"ok": True, "message": "配置已保存，部分配置需重启生效"}


# ────────────────────── helpers ──────────────────────


def _mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return "未设置" if not key else "***"
    return key[:4] + "****" + key[-4:]


def _read_env_file(path: Path) -> dict[str, str]:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def _write_env_file(path: Path, env: dict[str, str]):
    lines = [f"{k}={v}" for k, v in env.items()]
    path.write_text("\n".join(lines) + "\n")
