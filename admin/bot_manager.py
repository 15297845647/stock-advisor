"""Bot 配置生成 & cc-connect 进程管理 & 扫码绑定"""

import logging
import os
import subprocess
import time
from pathlib import Path

from infrastructure.database import get_connection

logger = logging.getLogger(__name__)

CC_CONFIG_PATH = Path(os.environ.get(
    "CC_CONFIG_PATH", os.path.expanduser("~/.cc-connect/config.toml")
))
QR_DIR = Path("/tmp/stock-advisor-qr")
SETUP_TIMEOUT = 300
WORK_DIR = os.environ.get("WORK_DIR", "/opt/stock-advisor")
PYTHON_CMD = os.environ.get("PYTHON_CMD", f"{WORK_DIR}/.venv/bin/python")
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
DB_PATH = os.environ.get("DB_PATH", f"{WORK_DIR}/data/stock_advisor.db")


async def regenerate_config():
    """从 DB 读取所有 active bot，生成 cc-connect config.toml"""
    conn = await get_connection()
    try:
        rows = await conn.execute_fetchall(
            "SELECT name, user_id, token, account_id FROM bots WHERE status = 'active'"
        )
        bots = [dict(r) for r in rows]
    finally:
        await conn.close()

    lines = ['language = "zh"', ""]

    for bot in bots:
        lines.append("[[projects]]")
        lines.append(f'name = "{bot["name"]}"')
        lines.append(f'work_dir = "{WORK_DIR}"')
        lines.append("")
        lines.append("[projects.agent]")
        lines.append('type = "acp"')
        lines.append("")
        lines.append("[projects.agent.options]")
        lines.append(f'work_dir = "{WORK_DIR}"')
        lines.append(f'command = "{PYTHON_CMD}"')
        lines.append('args = ["agent/main.py"]')

        env_parts = [
            f'BOT_USER_ID = "{bot["user_id"]}"',
            f'MINIMAX_API_KEY = "{MINIMAX_API_KEY}"',
            f'PYTHONPATH = "{WORK_DIR}"',
            f'DB_PATH = "{DB_PATH}"',
        ]
        lines.append("env = { " + ", ".join(env_parts) + " }")
        lines.append("")

        lines.append("[[projects.platforms]]")
        lines.append('type = "weixin"')
        lines.append("")
        lines.append("[projects.platforms.options]")
        lines.append('allow_from = "*"')
        lines.append('base_url = "https://ilinkai.weixin.qq.com"')

        if bot.get("token"):
            lines.append(f'token = "{bot["token"]}"')
        if bot.get("account_id"):
            lines.append(f'account_id = "{bot["account_id"]}"')

        lines.append("")

    CC_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CC_CONFIG_PATH.write_text("\n".join(lines), encoding="utf-8")
    logger.info("cc-connect config regenerated: %s (%d bots)", CC_CONFIG_PATH, len(bots))


def restart_cc_connect() -> str:
    """杀掉 cc-connect 并重启"""
    try:
        subprocess.run(["pkill", "cc-connect"], capture_output=True)
    except Exception:
        pass

    try:
        subprocess.Popen(
            ["cc-connect"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return "cc-connect 已重启"
    except Exception as e:
        logger.error("重启 cc-connect 失败: %s", e)
        return f"重启失败: {e}"


# ────────────────────── 扫码绑定 ──────────────────────

_setup_sessions: dict[str, dict] = {}


def parse_token_from_config(project_name: str) -> dict | None:
    """从 cc-connect 写入的 config.toml 解析指定 project 的 token/account_id"""
    if not CC_CONFIG_PATH.exists():
        return None

    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

    try:
        data = tomllib.loads(CC_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("解析 config.toml 失败: %s", e)
        return None

    for proj in data.get("projects", []):
        if proj.get("name") != project_name:
            continue
        for plat in proj.get("platforms", []):
            opts = plat.get("options", {})
            token = opts.get("token", "")
            account_id = opts.get("account_id", "")
            if token:
                return {"token": token, "account_id": account_id}
    return None


def start_setup(user_id: str) -> dict:
    """启动 cc-connect weixin setup 子进程，返回会话状态"""
    if user_id in _setup_sessions:
        session = _setup_sessions[user_id]
        if session["process"].poll() is None:
            return {"status": "waiting", "message": "绑定进行中，请等待扫码"}

    QR_DIR.mkdir(parents=True, exist_ok=True)
    qr_path = QR_DIR / f"qr-{user_id}.png"
    if qr_path.exists():
        qr_path.unlink()

    bot_name = f"bot-{user_id}"
    cmd = [
        "cc-connect", "weixin", "setup",
        "--project", bot_name,
        "--qr-image", str(qr_path),
        "--timeout", str(SETUP_TIMEOUT),
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError:
        return {"status": "failed", "message": "cc-connect 未安装或不在 PATH 中"}

    _setup_sessions[user_id] = {
        "process": proc,
        "qr_path": str(qr_path),
        "bot_name": bot_name,
        "started_at": time.time(),
    }

    logger.info("Bot setup started for user %s (pid=%d)", user_id, proc.pid)
    return {"status": "waiting", "message": "正在生成二维码..."}


def get_setup_status(user_id: str) -> dict:
    """检查绑定会话状态：waiting / qr_ready / success / failed / none"""
    session = _setup_sessions.get(user_id)
    if not session:
        return {"status": "none"}

    proc = session["process"]
    qr_path = Path(session["qr_path"])
    elapsed = time.time() - session["started_at"]

    # 进程还在运行
    if proc.poll() is None:
        if qr_path.exists() and qr_path.stat().st_size > 0:
            return {
                "status": "qr_ready",
                "elapsed": int(elapsed),
                "timeout": SETUP_TIMEOUT,
            }
        if elapsed > SETUP_TIMEOUT + 10:
            proc.kill()
            _cleanup_session(user_id)
            return {"status": "failed", "message": "超时，请重试"}
        return {"status": "waiting", "elapsed": int(elapsed)}

    # 进程已退出
    exit_code = proc.returncode
    if exit_code == 0:
        result = parse_token_from_config(session["bot_name"])
        _cleanup_session(user_id)
        if result and result.get("token"):
            return {
                "status": "success",
                "token": result["token"],
                "account_id": result.get("account_id", ""),
            }
        return {"status": "failed", "message": "扫码成功但未获取到 token，请重试"}

    stderr_out = ""
    try:
        stderr_out = proc.stderr.read().decode(errors="replace")[:500]
    except Exception:
        pass
    _cleanup_session(user_id)
    return {"status": "failed", "message": f"绑定失败 (exit={exit_code}): {stderr_out or '未知错误'}"}


def get_qr_path(user_id: str) -> str | None:
    """返回 QR 图片路径（如存在）"""
    session = _setup_sessions.get(user_id)
    if not session:
        return None
    qr_path = Path(session["qr_path"])
    if qr_path.exists() and qr_path.stat().st_size > 0:
        return str(qr_path)
    return None


def _cleanup_session(user_id: str):
    """清理会话和临时文件"""
    session = _setup_sessions.pop(user_id, None)
    if session:
        qr_path = Path(session["qr_path"])
        if qr_path.exists():
            try:
                qr_path.unlink()
            except Exception:
                pass
