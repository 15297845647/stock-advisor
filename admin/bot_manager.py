"""Bot 配置生成 & cc-connect 进程管理"""

import logging
import os
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
