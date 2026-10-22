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


def cmd_test_data(args):
    """测试 AKShare 数据拉取（排查数据问题）"""
    from infrastructure.akshare_client import AKShareClient

    async def _run():
        client = AKShareClient()
        code = args.code

        print(f"=== 测试 {code} 数据拉取 ===\n")

        print("[1] 实时行情...")
        q = await client.get_realtime_quote(code)
        if q:
            print(f"  ✅ {q.name} 价格:{q.price} 涨跌:{q.change_pct:+.2f}%")
        else:
            print("  ❌ 无数据")

        print("\n[2] 日K...")
        bars = await client.get_stock_history(code, days=5)
        print(f"  返回 {len(bars)} 条")
        for b in bars[-3:]:
            print(f"  {b.trade_date} 收:{b.close} 涨跌:{b.change_pct:+.2f}%")
        if not bars:
            print("  ❌ 无数据")

        print("\n[3] 资金流...")
        flows = await client.get_fund_flow(code)
        print(f"  返回 {len(flows)} 条")
        if not flows:
            print("  ❌ 无数据")

        print("\n[4] 新闻...")
        news = await client.get_stock_news(code, limit=3)
        print(f"  返回 {len(news)} 条")
        for n in news[:2]:
            print(f"  {n.title[:40]}")
        if not news:
            print("  ❌ 无数据")

        print("\n[5] 全量行情缓存...")
        try:
            from infrastructure.akshare_client import _get_spot_df
            df = await _get_spot_df()
            print(f"  ✅ {len(df)} 条")
        except Exception as e:
            print(f"  ❌ {e}")

        print("\n[6] 数据一致性校验...")
        consistency = await client.verify_quote_consistency(code)
        if consistency["consistent"]:
            print(f"  ✅ 多源一致 (差异 {consistency['max_diff_pct']}%)")
        else:
            print(f"  ⚠️ 数据不一致 (差异 {consistency['max_diff_pct']}%)")
        for src, price in consistency["sources"].items():
            print(f"    {src}: {price}")

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

    # test-data
    p_test = sub.add_parser("test-data", help="测试数据拉取（排查问题）")
    p_test.add_argument("code", help="股票代码，如 601360")

    p_research = sub.add_parser('research', help='新版可追溯个股研究（不发送消息）')
    p_research.add_argument('code')
    p_research.add_argument('--depth', choices=['quick','standard','deep'], default='standard')
    p_research.add_argument('--strategy', choices=['pullback','breakout'], default='pullback')
    p_research.add_argument('--no-model', action='store_true')
    p_research.add_argument('--force', action='store_true')
    p_screen = sub.add_parser('research-screen', help='对明确股票池进行新版筛选，最多30只')
    p_screen.add_argument('codes', nargs='+')
    p_screen.add_argument('--strategy', choices=['pullback','breakout'], default='pullback')
    p_screen.add_argument('--no-model', action='store_true')
    sub.add_parser('research-evaluate', help='更新已保存研究的5/10/20交易日前向评估')
    p_replay=sub.add_parser('research-replay',help='离线回放冻结的历史数据集，仅规则，不调用模型')
    p_replay.add_argument('dataset',help='JSON数据集路径；格式见docs/evidence/acceptance.md')
    p_replay.add_argument('--strategy',choices=['pullback','breakout'],default='pullback')

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
        "test-data": cmd_test_data,
        'research': cmd_research,
        'research-screen': cmd_research_screen,
        'research-evaluate': cmd_research_evaluate,
        'research-replay':cmd_research_replay,
    }
    cmds[args.command](args)


def cmd_research(args):
    from application.research_service import ResearchService
    async def execute():
        run=await ResearchService().analyze_run(args.code,args.depth,args.force,
            strategy_id=args.strategy,use_model=not args.no_model)
        print(run.report)
    asyncio.run(execute())


def cmd_research_screen(args):
    from application.research_selection_service import ResearchSelectionService, format_batch
    async def execute():
        result=await ResearchSelectionService().screen(args.codes,strategy_id=args.strategy,use_model=not args.no_model)
        print(format_batch(result))
    asyncio.run(execute())


def cmd_research_evaluate(args):
    from application.evaluation_service import EvaluationService
    from domain.execution_rules import summarize_evaluations
    from domain.data_quality import SH
    from datetime import datetime
    import json
    async def execute():
        results=await EvaluationService().evaluate_mature_runs(datetime.now(SH))
        for window in (5,10,20):
            for group in ('rules','rules_ai'):
                subset=[r for r in results if r.horizon_days==window and r.assumptions.get('group')==group]
                print(json.dumps({'window':window,'group':group,**summarize_evaluations(subset)},ensure_ascii=False))
    asyncio.run(execute())


def cmd_research_replay(args):
    import json
    from pathlib import Path
    from application.historical_research_service import HistoricalResearchService
    result=asyncio.run(HistoricalResearchService().replay(json.loads(Path(args.dataset).read_text()),args.strategy))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
