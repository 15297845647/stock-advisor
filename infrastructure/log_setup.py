"""统一日志配置 — 按天分割 + 自动清理"""

import logging
import logging.handlers
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
KEEP_DAYS = 7


def make_daily_handler(
    filename: str = "agent.log",
    keep_days: int = KEEP_DAYS,
) -> logging.handlers.TimedRotatingFileHandler:
    """创建按天轮转的日志 handler，自动保留 keep_days 天"""
    handler = logging.handlers.TimedRotatingFileHandler(
        LOG_DIR / filename,
        when="midnight",
        interval=1,
        backupCount=keep_days,
        encoding="utf-8",
    )
    handler.suffix = "%Y-%m-%d"
    handler.setFormatter(logging.Formatter(DEFAULT_FORMAT))
    return handler


def rotate_plain_log(filename: str = "cc-connect.log"):
    """轮转非 Python handler 管理的纯文本日志（如 cc-connect 重定向的文件）

    当文件 > 10MB 或日期变化时，重命名为 filename.YYYY-MM-DD 并清空原文件。
    """
    import datetime

    log_path = LOG_DIR / filename
    if not log_path.exists() or log_path.stat().st_size == 0:
        return

    today = datetime.date.today().isoformat()
    rotated = LOG_DIR / f"{filename}.{today}"

    # 今天已经轮转过了，只在文件过大时再轮转
    if rotated.exists() and log_path.stat().st_size < 10 * 1024 * 1024:
        return

    # 如果已有当天备份则追加
    if rotated.exists():
        with open(rotated, "a", encoding="utf-8") as dst:
            dst.write(log_path.read_text(encoding="utf-8", errors="replace"))
    else:
        log_path.rename(rotated)

    # 清空原文件（让后续写入继续用同一文件名）
    log_path.write_text("", encoding="utf-8")


def cleanup_old_logs(keep_days: int = KEEP_DAYS):
    """清理 LOG_DIR 下超过 keep_days 天的日志文件"""
    import time

    cutoff = time.time() - keep_days * 86400
    for f in LOG_DIR.iterdir():
        if not f.is_file():
            continue
        if f.suffix not in (".log", ".txt") and ".log." not in f.name:
            continue
        # 不删当天正在写入的主文件
        if f.name in ("agent.log", "cc-connect.log", "admin.log"):
            continue
        if f.stat().st_mtime < cutoff:
            try:
                f.unlink()
            except OSError:
                pass
