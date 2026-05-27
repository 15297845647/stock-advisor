#!/usr/bin/env python3
"""stock-advisor CLI — 统一入口"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("PYTHONPATH", str(PROJECT_ROOT))


def cmd_start(args):
    """启动 ACP Agent（由 cc-connect 调用）+ 管理后台 + 定时任务"""
    from agent.main import main
    asyncio.run(main())


def cmd_admin(args):
    """独立启动管理后台 Web 服务"""
    import uvicorn
    from agent.config import ADMIN_PORT
    from infrastructure.database import init_db

    asyncio.run(init_db())
    port = args.port or ADMIN_PORT
    print(f"管理后台启动中 → http://0.0.0.0:{port}")
    uvicorn.run("admin.server:app", host="0.0.0.0", port=port, log_level="info")


def cmd_init_db(args):
    """初始化数据库"""
    from infrastructure.database import init_db
    asyncio.run(init_db())
    print("数据库初始化完成")


def cmd_analyze(args):
    """手动触发单只股票分析"""
    from infrastructure.database import init_db
    from application.analysis_service import AnalysisService

    async def _run():
        await init_db()
        svc = AnalysisService()
        result = await svc.analyze_stock(args.code, force=True)
        print(result)

    asyncio.run(_run())


def cmd_push(args):
    """手动触发每日推送"""
    from infrastructure.database import init_db
    from scheduler.daily_push import DailyPushScheduler

    async def _run():
        await init_db()
        scheduler = DailyPushScheduler()
        await scheduler.run_daily_analysis()

    asyncio.run(_run())


def main():
    parser = argparse.ArgumentParser(
        prog="stock-advisor",
        description="A股/期货 AI 分析系统",
    )
    sub = parser.add_subparsers(dest="command")

    # start
    sub.add_parser("start", help="启动 ACP Agent（完整模式）")

    # admin
    p_admin = sub.add_parser("admin", help="仅启动管理后台")
    p_admin.add_argument("-p", "--port", type=int, default=None, help="端口号（默认读 .env）")

    # init-db
    sub.add_parser("init-db", help="初始化数据库表")

    # analyze
    p_analyze = sub.add_parser("analyze", help="手动分析单只股票")
    p_analyze.add_argument("code", help="股票代码，如 000001")

    # push
    sub.add_parser("push", help="手动触发每日推送")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    cmds = {
        "start": cmd_start,
        "admin": cmd_admin,
        "init-db": cmd_init_db,
        "analyze": cmd_analyze,
        "push": cmd_push,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
