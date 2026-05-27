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

CREATE TABLE IF NOT EXISTS user_watchlist (
    wechat_id TEXT REFERENCES users(wechat_id),
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (wechat_id, stock_code)
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

CREATE TABLE IF NOT EXISTS bots (
    name TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE,
    token TEXT DEFAULT '',
    account_id TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
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
