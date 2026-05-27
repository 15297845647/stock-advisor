import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_BASE_URL = os.getenv(
    "MINIMAX_BASE_URL", "https://api.minimaxi.com/anthropic/v1/messages"
)
MINIMAX_MODEL = os.getenv("MINIMAX_MODEL", "MiniMax-M2.7")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", str(PROJECT_ROOT / "data" / "stock_advisor.db"))

# AKShare 限频：每次请求间隔秒数
AKSHARE_REQUEST_INTERVAL = float(os.getenv("AKSHARE_REQUEST_INTERVAL", "2.5"))

# 对话历史保留轮数（每轮=user+assistant）
CHAT_HISTORY_ROUNDS = int(os.getenv("CHAT_HISTORY_ROUNDS", "5"))

# 长期记忆加载条数
MEMORY_LOAD_LIMIT = int(os.getenv("MEMORY_LOAD_LIMIT", "20"))

# 管理后台
ADMIN_PORT = int(os.getenv("ADMIN_PORT", "8900"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")

# .env 文件路径（用于后台动态修改配置）
ENV_FILE_PATH = PROJECT_ROOT / ".env"
