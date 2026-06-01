"""回测服务 — 从历史报告提取建议，对比实际行情，汇总准确率"""

import logging
from datetime import date, timedelta

from domain.backtest_engine import (
    BacktestRecord,
    compute_summary,
    evaluate_single,
    extract_decision_from_report,
    format_summary,
)
from infrastructure.akshare_client import AKShareClient
from infrastructure.database import get_connection

logger = logging.getLogger(__name__)


class BacktestService:
    def __init__(self):
        self.akshare = AKShareClient()

    async def run_backtest(self, days: int = 30, window: int = 5) -> str:
        """对近 N 天的历史建议做回测"""
        since = (date.today() - timedelta(days=days)).isoformat()

        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT stock_code, report_date, report_content FROM analysis_reports "
                "WHERE report_date >= ? ORDER BY report_date",
                (since,),
            )
        finally:
            await conn.close()

        if not rows:
            return f"近 {days} 天没有历史分析报告，无法回测。"

        # 提取有 [DECISION] 的报告
        records = []
        for r in rows:
            dec = extract_decision_from_report(r["report_content"])
            if not dec:
                continue

            action = dec.get("action", "")
            if not action or action == "持有":
                continue

            # 需要入场价：从报告日 K 线取收盘价
            bars = await self.akshare.get_stock_history(r["stock_code"], days=10)
            report_dt = r["report_date"]
            if isinstance(report_dt, str):
                report_dt = date.fromisoformat(report_dt)

            entry_price = None
            for b in bars:
                if b.trade_date == report_dt:
                    entry_price = b.close
                    break
            if not entry_price and bars:
                entry_price = bars[-1].close

            if not entry_price:
                continue

            records.append(BacktestRecord(
                stock_code=r["stock_code"],
                report_date=report_dt,
                action=action,
                confidence=float(dec.get("confidence", 0)),
                target_price=float(dec.get("target_price", 0)),
                stop_loss=float(dec.get("stop_loss", 0)),
                entry_price=entry_price,
            ))

        if not records:
            return f"近 {days} 天有报告但无可评估的买卖建议。"

        # 评估每条
        evals = []
        for rec in records:
            # 拉报告日之后的 K 线
            future_bars_raw = await self.akshare.get_stock_history(rec.stock_code, days=30)
            future_bars = [
                {"high": b.high, "low": b.low, "close": b.close, "date": b.trade_date}
                for b in future_bars_raw
                if b.trade_date > rec.report_date
            ]

            ev = evaluate_single(rec, future_bars, window_days=window)
            if ev:
                evals.append(ev)
                await self._save_eval(rec, ev)

        if not evals:
            return "建议均无法评估（可能时间太近，后续 K 线不足）。"

        summary = compute_summary(evals)
        return format_summary(summary)

    async def _save_eval(self, rec: BacktestRecord, ev):
        """回测结果写入 DB"""
        conn = await get_connection()
        try:
            await conn.execute(
                "INSERT OR REPLACE INTO backtest_results "
                "(stock_code, report_date, action, confidence, target_price, stop_loss, "
                "entry_price, exit_price, actual_return_pct, direction_correct, "
                "hit_target, hit_stop_loss, eval_window_days, evaluated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                (rec.stock_code, rec.report_date.isoformat(), rec.action,
                 rec.confidence, rec.target_price, rec.stop_loss,
                 rec.entry_price, ev.exit_price, ev.actual_return_pct,
                 1 if ev.direction_correct else 0,
                 1 if ev.hit_target else 0, 1 if ev.hit_stop_loss else 0,
                 ev.eval_window_days),
            )
            await conn.commit()
        finally:
            await conn.close()
