"""后台管理 API 路由"""

import os
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from admin.auth import login, verify_token
from agent.config import (
    ADMIN_PASSWORD,
    DB_PATH,
    ENV_FILE_PATH,
    all_provider_configs,
    get_llm_config,
    get_provider_env_map,
    get_tushare_config,
    update_llm_config,
    update_provider_config,
    update_tushare_config,
)
from infrastructure.database import get_connection

router = APIRouter(prefix="/api")

LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"


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
        "llm_model": get_llm_config()["model"],
        "llm_key_set": bool(get_llm_config()["api_key"]),
    }


# ────────────────────── 用户管理（含 Bot 绑定）──────────────────────


class CreateUserRequest(BaseModel):
    user_id: str
    nickname: str = ""
    risk_level: str = "moderate"
    trade_style: str = "swing"
    bot_token: str = ""
    bot_account_id: str = ""


class UpdateProfileRequest(BaseModel):
    nickname: str | None = None
    risk_level: str | None = None
    trade_style: str | None = None


class UpdateBotRequest(BaseModel):
    token: str
    account_id: str


@router.get("/users", dependencies=[Depends(verify_token)])
async def list_users():
    conn = await get_connection()
    try:
        rows = await conn.execute_fetchall(
            "SELECT u.*, "
            "  COUNT(w.stock_code) as watch_count, "
            "  b.name as bot_name, b.token as bot_token, "
            "  b.account_id as bot_account_id, b.status as bot_status "
            "FROM users u "
            "LEFT JOIN user_watchlist w ON u.wechat_id = w.wechat_id "
            "LEFT JOIN bots b ON u.wechat_id = b.user_id "
            "GROUP BY u.wechat_id ORDER BY u.created_at DESC"
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


@router.post("/users", dependencies=[Depends(verify_token)])
async def create_user(req: CreateUserRequest):
    """创建用户 + 关联 Bot，同时写 users 表和 bots 表"""
    from admin.bot_manager import regenerate_config

    uid = req.user_id.strip()
    if not uid:
        raise HTTPException(400, "用户ID不能为空")

    bot_name = f"bot-{uid}"
    bot_status = "active" if req.bot_token.strip() else "pending"

    conn = await get_connection()
    try:
        await conn.execute(
            "INSERT INTO users (wechat_id, nickname, risk_level, trade_style) VALUES (?, ?, ?, ?)",
            (uid, req.nickname or uid, req.risk_level, req.trade_style),
        )
        await conn.execute(
            "INSERT INTO bots (name, user_id, token, account_id, status) VALUES (?, ?, ?, ?, ?)",
            (bot_name, uid, req.bot_token.strip(), req.bot_account_id.strip(), bot_status),
        )
        await conn.commit()
    except Exception as e:
        raise HTTPException(400, f"创建失败（用户ID可能已存在）: {e}")
    finally:
        await conn.close()

    await regenerate_config()
    return {"ok": True, "user_id": uid, "bot_name": bot_name, "bot_status": bot_status}


@router.get("/users/{wechat_id}", dependencies=[Depends(verify_token)])
async def get_user_detail(wechat_id: str):
    conn = await get_connection()
    try:
        user_rows = await conn.execute_fetchall("SELECT * FROM users WHERE wechat_id = ?", (wechat_id,))
        if not user_rows:
            raise HTTPException(404, "用户不存在")

        bot_rows = await conn.execute_fetchall("SELECT * FROM bots WHERE user_id = ?", (wechat_id,))

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
            "bot": dict(bot_rows[0]) if bot_rows else None,
            "watchlist": [dict(r) for r in watchlist],
            "memories": [dict(r) for r in memories],
            "recent_chat": [dict(r) for r in reversed(list(recent_chat))],
        }
    finally:
        await conn.close()


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


@router.put("/users/{wechat_id}/bot", dependencies=[Depends(verify_token)])
async def update_user_bot(wechat_id: str, req: UpdateBotRequest):
    """更新用户的 Bot 绑定信息"""
    from admin.bot_manager import regenerate_config

    conn = await get_connection()
    try:
        await conn.execute(
            "UPDATE bots SET token = ?, account_id = ?, status = 'active' WHERE user_id = ?",
            (req.token.strip(), req.account_id.strip(), wechat_id),
        )
        await conn.commit()
    finally:
        await conn.close()

    await regenerate_config()
    return {"ok": True}


# ────────────────────── Bot 扫码绑定 ──────────────────────


@router.post("/users/{wechat_id}/bot/setup", dependencies=[Depends(verify_token)])
async def start_bot_setup(wechat_id: str):
    """启动扫码绑定流程，后台运行 cc-connect weixin setup"""
    from admin.bot_manager import start_setup
    result = start_setup(wechat_id)
    if result["status"] == "failed":
        raise HTTPException(500, result.get("message", "启动失败"))
    return result


@router.get("/users/{wechat_id}/bot/setup/status", dependencies=[Depends(verify_token)])
async def bot_setup_status(wechat_id: str):
    """轮询绑定状态。成功时自动写入 DB 并重新生成 config"""
    from admin.bot_manager import get_setup_status, regenerate_config

    result = get_setup_status(wechat_id)

    if result["status"] == "success":
        from admin.bot_manager import restart_cc_connect

        conn = await get_connection()
        try:
            await conn.execute(
                "UPDATE bots SET token = ?, account_id = ?, status = 'active' WHERE user_id = ?",
                (result["token"], result.get("account_id", ""), wechat_id),
            )
            await conn.commit()
        finally:
            await conn.close()
        await regenerate_config()
        restart_msg = restart_cc_connect()
        result["restart"] = restart_msg

    return result


@router.get("/users/{wechat_id}/bot/qr", dependencies=[Depends(verify_token)])
async def bot_qr_image(wechat_id: str):
    """返回绑定二维码 PNG 图片"""
    from admin.bot_manager import get_qr_path
    qr = get_qr_path(wechat_id)
    if not qr:
        raise HTTPException(404, "二维码尚未生成")
    return FileResponse(qr, media_type="image/png")


@router.delete("/users/{wechat_id}", dependencies=[Depends(verify_token)])
async def delete_user(wechat_id: str):
    """删除用户 + 关联 Bot + 重新生成 config"""
    from admin.bot_manager import regenerate_config

    conn = await get_connection()
    try:
        await conn.execute("DELETE FROM bots WHERE user_id = ?", (wechat_id,))
        await conn.execute("DELETE FROM user_positions WHERE wechat_id = ?", (wechat_id,))
        await conn.execute("DELETE FROM user_watchlist WHERE wechat_id = ?", (wechat_id,))
        await conn.execute("DELETE FROM user_memory WHERE wechat_id = ?", (wechat_id,))
        await conn.execute("DELETE FROM chat_history WHERE wechat_id = ?", (wechat_id,))
        await conn.execute("DELETE FROM users WHERE wechat_id = ?", (wechat_id,))
        await conn.commit()
    finally:
        await conn.close()

    await regenerate_config()
    return {"ok": True}


# ────────────────────── cc-connect 重启 ──────────────────────


@router.post("/restart", dependencies=[Depends(verify_token)])
async def api_restart_cc():
    from admin.bot_manager import restart_cc_connect
    msg = restart_cc_connect()
    return {"message": msg}


# ────────────────────── 配置管理 ──────────────────────


@router.get("/config", dependencies=[Depends(verify_token)])
async def get_config():
    """读取当前 LLM (含分 provider) + Tushare 配置（api_key 脱敏）"""
    cfg = get_llm_config()
    ts_cfg = get_tushare_config()
    providers = all_provider_configs()

    return {
        # 默认 LLM（向后兼容）
        "llm_api_key": _mask_key(cfg["api_key"]),
        "llm_base_url": cfg["base_url"],
        "llm_model": cfg["model"],
        # 各 provider 独立配置
        "providers": {
            name: {
                "api_key": _mask_key(pc.get("api_key", "")),
                "base_url": pc.get("base_url", ""),
                "has_key": bool(pc.get("api_key")),
            }
            for name, pc in providers.items()
        },
        # 其他
        "tushare_token": _mask_key(ts_cfg["token"]),
        "db_path": DB_PATH,
        "admin_port": int(os.getenv("ADMIN_PORT", "8900")),
    }


class ProviderConfigInput(BaseModel):
    """单个 provider 配置输入"""
    api_key: str | None = None
    base_url: str | None = None


class UpdateConfigRequest(BaseModel):
    # 默认 LLM 配置（向后兼容 + default provider 兜底）
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    # 各 provider 独立配置（推荐使用）
    providers: dict[str, ProviderConfigInput] | None = None
    # 其他
    tushare_token: str | None = None
    admin_password: str | None = None


@router.put("/config", dependencies=[Depends(verify_token)])
async def update_config(req: UpdateConfigRequest):
    """修改 LLM/Provider/Tushare 配置，立即热生效 + 持久化到 .env"""
    env_path = ENV_FILE_PATH
    env_map = _read_env_file(env_path)

    # 默认 LLM
    _apply_default_llm(env_map, req)
    # 各 provider
    _apply_providers(env_map, req.providers)
    # 其他
    if req.tushare_token:
        env_map["TUSHARE_TOKEN"] = req.tushare_token
    if req.admin_password:
        env_map["ADMIN_PASSWORD"] = req.admin_password

    _write_env_file(env_path, env_map)

    # 热更新运行时配置（LLMRouter 通过 getter 动态读，立即生效）
    update_llm_config(
        api_key=req.llm_api_key,
        base_url=req.llm_base_url,
        model=req.llm_model,
    )
    _hot_update_providers(req.providers)
    update_tushare_config(token=req.tushare_token)

    return {"ok": True, "message": "配置已保存并立即生效"}


def _apply_default_llm(env_map: dict, req: UpdateConfigRequest) -> None:
    """把默认 LLM 字段写入 env_map"""
    if req.llm_api_key:
        env_map["LLM_API_KEY"] = req.llm_api_key
    if req.llm_base_url:
        env_map["LLM_BASE_URL"] = req.llm_base_url
    if req.llm_model:
        env_map["LLM_MODEL"] = req.llm_model


def _apply_providers(
    env_map: dict, providers: dict[str, ProviderConfigInput] | None,
) -> None:
    """把各 provider 字段写入 env_map"""
    if not providers:
        return

    env_key_map = get_provider_env_map()
    for name, pcfg in providers.items():
        if name not in env_key_map:
            continue
        keys = env_key_map[name]
        if pcfg.api_key:
            env_map[keys["api_key"]] = pcfg.api_key
        if pcfg.base_url:
            env_map[keys["base_url"]] = pcfg.base_url


def _hot_update_providers(
    providers: dict[str, ProviderConfigInput] | None,
) -> None:
    """热更新运行时 provider 配置"""
    if not providers:
        return

    for name, pcfg in providers.items():
        update_provider_config(
            name, api_key=pcfg.api_key, base_url=pcfg.base_url,
        )


# ────────────────────── 策略配置 ──────────────────────


class UpdateStrategyConfigRequest(BaseModel):
    lookback_days: int | None = None
    max_boards: int | None = None
    volume_ratio_min: float | None = None
    candidate_cap: int | None = None
    output_count: int | None = None
    auto_watchlist: bool | None = None
    advice_rule3: str | None = None
    advice_rule4: str | None = None
    advice_rule5: str | None = None


@router.get("/strategy-config", dependencies=[Depends(verify_token)])
async def get_strategy_config():
    """读取养家选股策略配置（缺字段以默认值补全）"""
    from application.config_service import ConfigService

    cfg = await ConfigService().get_yangjia_config()
    return cfg.to_dict()


@router.put("/strategy-config", dependencies=[Depends(verify_token)])
async def update_strategy_config(req: UpdateStrategyConfigRequest):
    """更新养家选股策略配置，立即生效"""
    from application.config_service import ConfigService

    data = {k: v for k, v in req.model_dump().items() if v is not None}
    cfg = await ConfigService().save_yangjia_config(data)
    return {"ok": True, "config": cfg.to_dict()}


class UpdateMidLongConfigRequest(BaseModel):
    require_ma_bull: bool | None = None
    require_uptrend: bool | None = None
    min_roe: float | None = None
    max_pe: float | None = None
    min_revenue_growth: float | None = None
    candidate_cap: int | None = None
    output_count: int | None = None
    auto_watchlist: bool | None = None
    advice_hold: str | None = None
    advice_stop: str | None = None
    advice_add: str | None = None


@router.get("/strategy-config/midlong", dependencies=[Depends(verify_token)])
async def get_midlong_config():
    """读取中长线选股策略配置（缺字段以默认值补全）"""
    from application.config_service import ConfigService

    cfg = await ConfigService().get_midlong_config()
    return cfg.to_dict()


@router.put("/strategy-config/midlong", dependencies=[Depends(verify_token)])
async def update_midlong_config(req: UpdateMidLongConfigRequest):
    """更新中长线选股策略配置，立即生效"""
    from application.config_service import ConfigService

    data = {k: v for k, v in req.model_dump().items() if v is not None}
    cfg = await ConfigService().save_midlong_config(data)
    return {"ok": True, "config": cfg.to_dict()}


# ────────────────────── 日志查看 ──────────────────────


@router.get("/logs", dependencies=[Depends(verify_token)])
async def list_log_files():
    """列出可用日志文件（含轮转后的历史文件 agent.log.2026-06-11）"""
    if not LOG_DIR.exists():
        return []
    files = []
    for f in sorted(LOG_DIR.iterdir(), reverse=True):
        if not f.is_file():
            continue
        if ".log" not in f.name:
            continue
        files.append({
            "name": f.name,
            "size_kb": round(f.stat().st_size / 1024, 1),
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        })
    return files


@router.get("/logs/content", dependencies=[Depends(verify_token)])
async def read_log_content(
    file: str = Query("agent.log", description="日志文件名"),
    lines: int = Query(300, ge=10, le=5000, description="返回行数"),
    level: str = Query("", description="按级别过滤: INFO/WARNING/ERROR"),
    search: str = Query("", description="关键词搜索"),
):
    """读取日志文件末尾内容，支持级别过滤和关键词搜索"""
    # 安全校验：防止路径穿越
    if ".." in file or "/" in file or "\\" in file:
        raise HTTPException(400, "非法文件名")

    log_path = LOG_DIR / file
    if not log_path.exists():
        raise HTTPException(404, f"日志文件不存在: {file}")

    all_lines = _tail_file(log_path, max_lines=lines * 3)

    if level:
        level_upper = level.upper()
        all_lines = [ln for ln in all_lines if f"[{level_upper}]" in ln]

    if search:
        all_lines = [ln for ln in all_lines if search.lower() in ln.lower()]

    result_lines = all_lines[-lines:]

    return {
        "file": file,
        "total_lines": len(result_lines),
        "content": result_lines,
    }


def _tail_file(path: Path, max_lines: int = 1000) -> list[str]:
    """高效读取文件末尾 N 行（避免大文件全量加载）"""
    try:
        size = path.stat().st_size
        if size == 0:
            return []
        # 小文件直接全读
        if size < 2 * 1024 * 1024:
            return path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
        # 大文件从尾部读
        chunk_size = min(size, max_lines * 500)
        with open(path, "rb") as f:
            f.seek(max(0, size - chunk_size))
            data = f.read().decode("utf-8", errors="replace")
        lines = data.splitlines()
        if len(lines) > 0 and f.tell() != size:
            lines = lines[1:]
        return lines[-max_lines:]
    except Exception:
        return []


# ────────────────────── 数据源管理 ──────────────────────


def _get_ds_service():
    """延迟获取 DataSourceAdminService 单例"""
    from application.data_source_admin_service import DataSourceAdminService
    if not hasattr(_get_ds_service, "_instance"):
        _get_ds_service._instance = DataSourceAdminService()
    return _get_ds_service._instance


@router.get("/data-sources/summary", dependencies=[Depends(verify_token)])
async def ds_summary(hours: int = Query(24, ge=1, le=720)):
    """概览卡片数据"""
    return await _get_ds_service().get_summary(hours=hours)


@router.get("/data-sources/health", dependencies=[Depends(verify_token)])
async def ds_health():
    """所有 source 的实时健康状态"""
    return await _get_ds_service().get_health_status()


@router.get("/data-sources/stats", dependencies=[Depends(verify_token)])
async def ds_stats(hours: int = Query(24, ge=1, le=720)):
    """按 source 汇总近 N 小时统计"""
    return await _get_ds_service().get_stats(hours=hours)


@router.get("/data-sources/stats/by-operation", dependencies=[Depends(verify_token)])
async def ds_stats_by_operation(hours: int = Query(24, ge=1, le=720)):
    """按 operation × source 展示降级链命中分布"""
    return await _get_ds_service().get_stats_by_operation(hours=hours)


@router.get("/data-sources/timeline", dependencies=[Depends(verify_token)])
async def ds_timeline(hours: int = Query(24, ge=1, le=168)):
    """近 N 小时的分桶调用趋势"""
    return await _get_ds_service().get_timeline(hours=hours)


@router.get("/data-sources/recent-failures", dependencies=[Depends(verify_token)])
async def ds_recent_failures(limit: int = Query(50, ge=1, le=500)):
    """最近 N 条失败日志"""
    return {"failures": await _get_ds_service().get_recent_failures(limit=limit)}


@router.post(
    "/data-sources/{source_name}/reset-breaker",
    dependencies=[Depends(verify_token)],
)
async def ds_reset_breaker(source_name: str):
    """手动重置指定 source 的熔断器"""
    result = await _get_ds_service().reset_breaker(source_name)
    if not result.get("success"):
        raise HTTPException(404, result.get("message", "重置失败"))
    return result


@router.post("/data-sources/probe", dependencies=[Depends(verify_token)])
async def ds_probe():
    """并发探活所有 source"""
    return await _get_ds_service().probe_all()


@router.post("/data-sources/reload-config", dependencies=[Depends(verify_token)])
async def ds_reload_config():
    """热重载 data_sources.yaml"""
    result = await _get_ds_service().reload_config()
    if not result.get("success"):
        raise HTTPException(500, result.get("message", "重载失败"))
    return result


@router.get("/data-sources/config", dependencies=[Depends(verify_token)])
async def ds_get_config():
    """返回当前生效的配置"""
    return await _get_ds_service().get_current_config()


# ────────────────────── LLM 用量统计 ──────────────────────


def _get_usage_service():
    from application.usage_statistics_service import UsageStatisticsService
    if not hasattr(_get_usage_service, "_instance"):
        _get_usage_service._instance = UsageStatisticsService()
    return _get_usage_service._instance


@router.get("/usage/dashboard", dependencies=[Depends(verify_token)])
async def usage_dashboard(hours: int = Query(24, ge=1, le=720)):
    """LLM 用量仪表盘"""
    return await _get_usage_service().get_dashboard(hours=hours)


@router.get("/usage/daily", dependencies=[Depends(verify_token)])
async def usage_daily(days: int = Query(30, ge=1, le=365)):
    """LLM 每日成本趋势"""
    return await _get_usage_service().get_daily_series(days=days)


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
