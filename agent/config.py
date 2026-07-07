import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# LLM 配置（DeepSeek / OpenAI 兼容 API）— 运行时可被后台热更新
_llm_config = {
    "api_key": os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or os.getenv("MINIMAX_API_KEY", ""),
    "base_url": os.getenv("LLM_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    "model": os.getenv("LLM_MODEL") or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
}


def get_llm_config() -> dict:
    """获取当前 LLM 配置（运行时可变）"""
    return _llm_config.copy()


def update_llm_config(api_key: str | None = None, base_url: str | None = None, model: str | None = None):
    """热更新 LLM 配置，立即对后续请求生效"""
    if api_key is not None:
        _llm_config["api_key"] = api_key
    if base_url is not None:
        _llm_config["base_url"] = base_url
    if model is not None:
        _llm_config["model"] = model


# 兼容旧代码直接引用（首次加载时的值）
LLM_API_KEY = _llm_config["api_key"]
LLM_BASE_URL = _llm_config["base_url"]
LLM_MODEL = _llm_config["model"]
MINIMAX_API_KEY = LLM_API_KEY
MINIMAX_BASE_URL = LLM_BASE_URL
MINIMAX_MODEL = LLM_MODEL

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", str(PROJECT_ROOT / "data" / "stock_advisor.db"))

# AKShare 限频：每次请求间隔秒数
AKSHARE_REQUEST_INTERVAL = float(os.getenv("AKSHARE_REQUEST_INTERVAL", "1.0"))

# 对话历史保留轮数（每轮=user+assistant）
CHAT_HISTORY_ROUNDS = int(os.getenv("CHAT_HISTORY_ROUNDS", "5"))

# 长期记忆加载条数
MEMORY_LOAD_LIMIT = int(os.getenv("MEMORY_LOAD_LIMIT", "20"))

# 管理后台
ADMIN_PORT = int(os.getenv("ADMIN_PORT", "8900"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")

# .env 文件路径（用于后台动态修改配置）
ENV_FILE_PATH = PROJECT_ROOT / ".env"
