"""每日定时推送 — 收盘分析 + 持仓策略（开盘前 / 收盘后）

嵌入 agent 主进程，通过 APScheduler 触发：
- 09:00  开盘前持仓策略推送
- 15:30  收盘后自选分析 + 持仓策略推送
"""

import logging
import subprocess
from datetime import date

from application.analysis_service import AnalysisService
from application.position_service import PositionService
from domain.prompt_builder import build_position_strategy_prompt, build_system_prompt
from domain.stock_analyzer import analyze_technical
from infrastructure.akshare_client import AKShareClient
from infrastructure.minimax_client import MiniMaxClient
from repository.position_repository import PositionRepository
from repository.user_repository import UserRepository

logger = logging.getLogger(__name__)


class DailyPushScheduler:
    def __init__(self):
        self.analysis = AnalysisService()
        self.position_svc = PositionService()
        self.position_repo = PositionRepository()
        self.user_repo = UserRepository()
        self.akshare = AKShareClient()
        self.minimax = MiniMaxClient()

    # ── 收盘后 15:30 ──

    async def run_daily_analysis(self):
        """交易日收盘后：自选分析 + 持仓策略"""
        if not await self.akshare.is_trade_day(date.today()):
            logger.info("非交易日，跳过每日分析")
            return

        logger.info("开始收盘后推送...")

        # 自选股分析（原逻辑）
        await self._push_watchlist_analysis()

        # 持仓策略
        await self._push_position_strategy("收盘后操作策略")

        logger.info("收盘后推送完成")

    async def _push_watchlist_analysis(self):
        """自选股收盘分析摘要"""
        users = await self.user_repo.get_all_users_with_watchlist()
        for wechat_id, stock_codes in users:
            await self._analyze_and_push(wechat_id, stock_codes)
        logger.info("自选分析完成，共 %d 个用户", len(users))

    async def _analyze_and_push(self, wechat_id: str, stock_codes: list[str]):
        summaries = []
        for code in stock_codes:
            try:
                report = await self.analysis.analyze_stock(code, force=True)
                summary = report[:200] + "..." if len(report) > 200 else report
                summaries.append(f"【{code}】\n{summary}")
            except Exception as e:
                logger.error("分析 %s 失败: %s", code, e)
                summaries.append(f"【{code}】分析失败")

        if not summaries:
            return

        message = f"📊 {date.today()} 收盘分析\n\n" + "\n\n".join(summaries)
        message += "\n\n回复股票代码可查看详细分析。"
        self._send_via_cc_connect(message)

    # ── 开盘前 09:00 ──

    async def run_premarket_strategy(self):
        """交易日开盘前：持仓策略推送"""
        if not await self.akshare.is_trade_day(date.today()):
            logger.info("非交易日，跳过盘前策略")
            return

        logger.info("开始盘前策略推送...")
        await self._push_position_strategy("开盘前操作策略")
        logger.info("盘前策略推送完成")

    # ── 持仓策略核心 ──

    async def _push_position_strategy(self, push_type: str):
        """为所有有持仓的用户生成并推送策略"""
        users_with_pos = await self.position_repo.get_all_users_with_positions()

        for wechat_id, positions in users_with_pos:
            try:
                strategy = await self._generate_strategy(wechat_id, positions, push_type)
                if strategy:
                    self._send_via_cc_connect(strategy)
            except Exception as e:
                logger.error("用户 %s 策略生成失败: %s", wechat_id, e)

        logger.info("持仓策略推送完成，共 %d 个用户", len(users_with_pos))

    async def _generate_strategy(
        self, wechat_id: str, positions: list[dict], push_type: str
    ) -> str | None:
        """为单个用户生成持仓操作策略"""
        # 获取用户画像
        profile = await self.user_repo.ensure_user(wechat_id)

        # 持仓摘要
        position_summary = await self.position_svc.build_position_summary(positions)

        # 大盘概览
        market_overview = await self.analysis.get_market_overview()

        # 各持仓股技术指标
        tech_lines = []
        for p in positions:
            code = p["stock_code"]
            bars = await self.akshare.get_stock_history(code, days=60)
            tech = analyze_technical(bars) if bars else None
            if tech:
                tech_lines.append(
                    f"【{p['stock_name']}({code})】\n"
                    f"  趋势: {tech.trend} | MA5={tech.ma5} MA10={tech.ma10} MA20={tech.ma20}\n"
                    f"  MACD: {tech.macd:.3f} 信号线: {tech.macd_signal:.3f}\n"
                    f"  RSI(14): {tech.rsi_14:.1f} | KDJ: K={tech.kdj_k:.1f} D={tech.kdj_d:.1f} J={tech.kdj_j:.1f}\n"
                    f"  支撑: {tech.support} | 压力: {tech.resistance}"
                )

        technical_data = "\n".join(tech_lines) if tech_lines else "技术数据不足"

        # 构造 prompt → MiniMax 生成策略
        user_prompt = build_position_strategy_prompt(
            push_type=push_type,
            risk_level=profile.risk_level,
            trade_style=profile.trade_style,
            position_summary=position_summary,
            market_overview=market_overview,
            technical_data=technical_data,
        )

        system = build_system_prompt()
        strategy = await self.minimax.chat(
            system_prompt=system,
            messages=[{"role": "user", "content": user_prompt}],
        )

        header = "🌅 开盘前策略" if "开盘前" in push_type else "🌆 收盘后策略"
        return f"{header} — {date.today()}\n\n{strategy}"

    @staticmethod
    def _send_via_cc_connect(text: str):
        """通过 cc-connect CLI 发送消息"""
        try:
            subprocess.run(
                ["cc-connect", "send", "--text", text],
                timeout=30,
                capture_output=True,
            )
        except FileNotFoundError:
            logger.warning("cc-connect CLI 未安装，无法推送消息")
        except subprocess.TimeoutExpired:
            logger.warning("cc-connect send 超时")
        except Exception as e:
            logger.error("推送失败: %s", e)


def setup_scheduler() -> None:
    """配置并启动 APScheduler"""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = AsyncIOScheduler()
    pusher = DailyPushScheduler()

    # 开盘前 09:00 — 持仓策略
    scheduler.add_job(
        pusher.run_premarket_strategy,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=0),
        id="premarket_strategy",
        name="盘前持仓策略推送",
        replace_existing=True,
    )

    # 收盘后 15:30 — 自选分析 + 持仓策略
    scheduler.add_job(
        pusher.run_daily_analysis,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=30),
        id="daily_analysis",
        name="收盘分析+持仓策略推送",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("定时任务已启动：盘前 09:00 持仓策略 | 收盘 15:30 分析+策略")
