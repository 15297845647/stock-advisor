import logging
from pathlib import Path

import aiosqlite

from agent.config import DB_PATH

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    wechat_id TEXT PRIMARY KEY,
    nickname TEXT,
    risk_level TEXT DEFAULT 'moderate',
    trade_style TEXT DEFAULT 'swing',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wechat_id TEXT REFERENCES users(wechat_id),
    content TEXT NOT NULL,
    category TEXT DEFAULT 'preference',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wechat_id TEXT REFERENCES users(wechat_id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS analysis_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    report_date DATE NOT NULL,
    report_content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stock_daily_cache (
    stock_code TEXT NOT NULL,
    trade_date DATE NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, amount REAL, change_pct REAL,
    PRIMARY KEY (stock_code, trade_date)
);

CREATE TABLE IF NOT EXISTS user_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wechat_id TEXT REFERENCES users(wechat_id),
    stock_code TEXT NOT NULL,
    stock_name TEXT DEFAULT '',
    direction TEXT DEFAULT 'long',
    shares INTEGER NOT NULL,
    cost_price REAL NOT NULL,
    open_date DATE NOT NULL,
    close_date DATE,
    close_price REAL,
    status TEXT DEFAULT 'open',
    note TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    report_date DATE NOT NULL,
    action TEXT NOT NULL,
    confidence REAL DEFAULT 0,
    target_price REAL DEFAULT 0,
    stop_loss REAL DEFAULT 0,
    entry_price REAL NOT NULL,
    exit_price REAL,
    actual_return_pct REAL,
    direction_correct INTEGER,
    hit_target INTEGER DEFAULT 0,
    hit_stop_loss INTEGER DEFAULT 0,
    eval_window_days INTEGER DEFAULT 5,
    evaluated_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bots (
    name TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE,
    token TEXT DEFAULT '',
    account_id TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS strategy_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 数据源调用日志（供 Admin 观测降级链 + 命中率）
CREATE TABLE IF NOT EXISTS data_source_call_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,           -- quote/kline/fund_flow/sector_flow/news
    source TEXT NOT NULL,              -- akshare.spot_em / tushare.quote / ...
    stock_code TEXT,                   -- 可为空（如 sector_flow 无关个股）
    success INTEGER NOT NULL,          -- 0/1
    latency_ms INTEGER,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dsc_log_ts ON data_source_call_log(created_at);
CREATE INDEX IF NOT EXISTS idx_dsc_log_source ON data_source_call_log(source, created_at);
CREATE INDEX IF NOT EXISTS idx_dsc_log_success ON data_source_call_log(success, created_at);

-- LLM 用量记录（供 Admin 查看 tokens / 成本 / 用户分布）
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wechat_id TEXT,                    -- 归属用户（可空 = 系统调用）
    task_type TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    cost_cny REAL DEFAULT 0.0,
    latency_ms INTEGER,
    success INTEGER DEFAULT 1,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_ts ON llm_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_usage_user ON llm_usage(wechat_id, created_at);
CREATE INDEX IF NOT EXISTS idx_llm_usage_task ON llm_usage(task_type, created_at);

-- 推荐记录（供回测）
CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wechat_id TEXT,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    recommended_at TIMESTAMP NOT NULL,
    recommend_price REAL,
    target_price REAL,
    stop_loss REAL,
    risk_score INTEGER,
    reason TEXT,
    intent_json TEXT,
    adjusted INTEGER DEFAULT 0,
    outcome TEXT,
    outcome_price REAL,
    outcome_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_rec_user_ts ON recommendations(wechat_id, recommended_at);
CREATE INDEX IF NOT EXISTS idx_rec_code_ts ON recommendations(stock_code, recommended_at);

-- 股票新闻（预拉取入库）
CREATE TABLE IF NOT EXISTS stock_news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT,
    publish_time TEXT NOT NULL,
    source TEXT,
    url TEXT,
    news_type TEXT DEFAULT 'news',
    hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_news_code_time ON stock_news(stock_code, publish_time DESC);

-- 分析报告分享链接（PDF 短链）
CREATE TABLE IF NOT EXISTS report_shares (
    share_token TEXT PRIMARY KEY,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    depth TEXT,                        -- QUICK / STANDARD / DEEP
    report_content TEXT,               -- 完整报告 markdown
    pdf_path TEXT,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    view_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_report_shares_expire ON report_shares(expires_at);
"""


async def get_connection() -> aiosqlite.Connection:
    db_dir = Path(DB_PATH).parent
    db_dir.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    return conn


async def init_db():
    """首次启动时建表"""
    conn = await get_connection()
    try:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        logger.info("Database initialized at %s", DB_PATH)
    finally:
        await conn.close()
