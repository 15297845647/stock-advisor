import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ────────────────────── 全局默认 LLM 配置 ──────────────────────
# 用于 default provider（无独立配置时兜底），运行时可热更新
_llm_config = {
    "api_key": os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or os.getenv("MINIMAX_API_KEY", ""),
    "base_url": os.getenv("LLM_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    "model": os.getenv("LLM_MODEL") or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
}

# ────────────────────── 各 LLM Provider 独立配置 ──────────────────────
# 每个 provider 可独立配置 api_key / base_url
# 各 provider 缺失时降级用 _llm_config
_provider_configs: dict[str, dict] = {
    "deepseek": {
        "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
    },
    "qwen": {
        "api_key": os.getenv("DASHSCOPE_API_KEY", ""),
        "base_url": os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    },
    "minimax": {
        "api_key": os.getenv("MINIMAX_API_KEY", ""),
        "base_url": os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1"),
    },
}

# provider name → 对应 .env 环境变量名（供落盘时使用）
_PROVIDER_ENV_MAP = {
    "deepseek": {"api_key": "DEEPSEEK_API_KEY", "base_url": "DEEPSEEK_BASE_URL"},
    "qwen":     {"api_key": "DASHSCOPE_API_KEY", "base_url": "DASHSCOPE_BASE_URL"},
    "minimax":  {"api_key": "MINIMAX_API_KEY", "base_url": "MINIMAX_BASE_URL"},
}

# ────────────────────── Tushare 配置 ──────────────────────
_tushare_config = {
    "token": os.getenv("TUSHARE_TOKEN", ""),
}


def get_llm_config() -> dict:
    """获取默认 LLM 配置（运行时可变）"""
    return _llm_config.copy()


def get_provider_config(name: str) -> dict:
    """
    获取指定 provider 的配置（运行时可变）
    未配置的 provider 返回空 dict + default fallback。
    """
    cfg = _provider_configs.get(name)
    if cfg is not None:
        return cfg.copy()
    return {"api_key": "", "base_url": ""}


def all_provider_configs() -> dict[str, dict]:
    """所有 provider 配置的浅拷贝"""
    return {n: c.copy() for n, c in _provider_configs.items()}


def get_provider_env_map() -> dict[str, dict]:
    """provider → 环境变量名映射（供 admin 写 .env 用）"""
    return {n: v.copy() for n, v in _PROVIDER_ENV_MAP.items()}


def get_tushare_config() -> dict:
    """获取当前 Tushare 配置（运行时可变）"""
    return _tushare_config.copy()


def update_llm_config(api_key: str | None = None, base_url: str | None = None, model: str | None = None):
    """热更新默认 LLM 配置，立即对后续请求生效"""
    if api_key is not None:
        _llm_config["api_key"] = api_key
    if base_url is not None:
        _llm_config["base_url"] = base_url
    if model is not None:
        _llm_config["model"] = model


def update_provider_config(
    name: str, api_key: str | None = None, base_url: str | None = None,
) -> None:
    """
    热更新指定 provider 配置
    立即对后续 LLMRouter 调用生效（provider 动态读取，无需重启）
    """
    cfg = _provider_configs.setdefault(name, {"api_key": "", "base_url": ""})
    if api_key is not None:
        cfg["api_key"] = api_key
    if base_url is not None:
        cfg["base_url"] = base_url


def update_tushare_config(token: str | None = None):
    """热更新 Tushare 配置"""
    if token is not None:
        _tushare_config["token"] = token


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
