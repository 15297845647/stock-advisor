"""Bot 管理：增删 bot、生成 cc-connect config.toml、重启 cc-connect"""

import logging
import os
import signal
import subprocess
from pathlib import Path

from infrastructure.database import get_connection

logger = logging.getLogger(__name__)

CC_CONFIG_PATH = Path(os.environ.get(
    "CC_CONFIG_PATH", os.path.expanduser("~/.cc-connect/config.toml")
))
WORK_DIR = os.environ.get("WORK_DIR", "/opt/stock-advisor")
PYTHON_CMD = os.environ.get("PYTHON_CMD", f"{WORK_DIR}/.venv/bin/python")
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
DB_PATH = os.environ.get("DB_PATH", f"{WORK_DIR}/data/stock_advisor.db")


async def list_bots() -> list[dict]:
    conn = await get_connection()
    try:
        rows = await conn.execute_fetchall(
            "SELECT name, user_id, token, account_id, status, created_at FROM bots ORDER BY created_at"
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def add_bot(user_id: str, token: str = "", account_id: str = "") -> dict:
    """添加新 bot，写入DB，重新生成 config.toml"""
    name = f"bot-{user_id}"
    status = "active" if token else "pending"
    conn = await get_connection()
    try:
        await conn.execute(
            "INSERT INTO bots (name, user_id, token, account_id, status) VALUES (?, ?, ?, ?, ?)",
            (name, user_id, token, account_id, status),
        )
        await conn.commit()
    finally:
        await conn.close()

    await regenerate_config()
    return {"name": name, "user_id": user_id, "status": status}


async def delete_bot(name: str):
    conn = await get_connection()
    try:
        await conn.execute("DELETE FROM bots WHERE name = ?", (name,))
        await conn.commit()
    finally:
        await conn.close()

    await regenerate_config()


async def update_bot_token(name: str, token: str, account_id: str):
    """QR 扫码绑定后更新 token"""
    conn = await get_connection()
    try:
        await conn.execute(
            "UPDATE bots SET token = ?, account_id = ?, status = 'active' WHERE name = ?",
            (token, account_id, name),
        )
        await conn.commit()
    finally:
        await conn.close()

    await regenerate_config()


async def regenerate_config():
    """从 DB 读取所有 bot，生成 cc-connect config.toml"""
    bots = await list_bots()

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


def get_setup_command(bot_name: str) -> str:
    """返回绑定微信的命令"""
    return f"cc-connect weixin setup --project {bot_name}"
