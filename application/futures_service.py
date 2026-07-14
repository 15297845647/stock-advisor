"""期货分析服务 — Tushare 优先，akshare 降级

流程：识别品种 → Tushare 拉主力合约 + 日线 → 失败降级 akshare → 技术指标 → LLM 分析
"""

import logging

from domain.stock_analyzer import analyze_technical
from domain.models.stock import StockDailyBar
from infrastructure.minimax_client import MiniMaxClient
from infrastructure.tushare_client import TushareClient
from domain.prompt_builder import _load_template

logger = logging.getLogger(__name__)


class FuturesAnalysisService:
    def __init__(self):
        self.tushare = TushareClient()
        self.llm = MiniMaxClient()

    async def analyze(self, text: str) -> str:
        """从用户文本识别期货品种并分析"""
        resolved = self.tushare.resolve_futures(text)
        if not resolved:
            return (
                "未识别到期货品种。支持：螺纹钢、铁矿石、原油、黄金、白银、铜、"
                "豆粕、焦煤、焦炭、纯碱、玻璃、欧线集运、棕榈油、橡胶、PTA、生猪等。\n"
                "也支持指定合约月份，如：EC2408、RB2501。"
            )

        symbol, exchange, name, contract_month = resolved
        if contract_month:
            logger.info("[FuturesAnalysis] 指定合约: %s%s.%s", symbol, contract_month, exchange)
        else:
            logger.info("[FuturesAnalysis] 品种: %s(%s.%s) → 拉主力合约", name, symbol, exchange)

        # Tushare 优先拉数据
        bars_raw, contract_code = await self._fetch_via_tushare(
            symbol, exchange, name, contract_month,
        )

        # Tushare 失败 → 降级 akshare（仅主力/连续合约可降级，指定月份无法降级）
        if not bars_raw and not contract_month:
            logger.info("[FuturesAnalysis] Tushare 无数据，降级 akshare")
            bars_raw, contract_code = await self._fetch_via_akshare(text, symbol, name)
        elif not bars_raw and contract_month:
            return (
                f"{name}{contract_month}合约暂无数据。\n"
                f"请确认合约月份有效，或尝试不指定月份查看主力合约。"
            )

        if not bars_raw:
            return (
                f"{name}暂无行情数据。\n"
                f"可能原因：Tushare Token 未配置/积分不足，且 akshare 无此品种数据。"
            )

        # 转 StockDailyBar 复用技术指标计算
        bars = self._to_daily_bars(bars_raw)
        tech = analyze_technical(bars) if len(bars) >= 10 else None
        latest = bars_raw[-1]

        context = self._build_context(name, contract_code, latest, bars_raw, tech)

        try:
            system = _load_template("unified.txt")
            raw = await self.llm.chat(
                system_prompt=system,
                messages=[{"role": "user", "content": context}],
            )
            return raw or context
        except Exception as e:
            logger.error("[FuturesAnalysis] LLM 分析失败: %s，返回原始数据", e)
            return context

    async def _fetch_via_tushare(
        self, symbol: str, exchange: str, name: str,
        contract_month: str | None = None,
    ) -> tuple[list[dict], str]:
        """Tushare 拉日线。指定月份用具体合约，否则拉主力合约。"""
        try:
            if contract_month:
                # 指定合约: EC2408.INE
                ts_code = f"{symbol}{contract_month}.{exchange}"
            else:
                # 主力合约映射
                ts_code = await self.tushare.get_main_contract(symbol, exchange)
                if not ts_code:
                    ts_code = f"{symbol}0.{exchange}"
                    logger.info("[FuturesAnalysis] 主力合约获取失败，降级 %s", ts_code)

            bars = await self.tushare.get_futures_daily(ts_code, days=60)
            return bars, ts_code
        except Exception as e:
            logger.warning("[FuturesAnalysis] Tushare 数据异常: %s", e)
            return [], ""

    async def _fetch_via_akshare(
        self, text: str, symbol: str, name: str,
    ) -> tuple[list[dict], str]:
        """akshare 降级：拉连续合约日线"""
        try:
            from infrastructure.akshare_client import AKShareClient
            ak_client = AKShareClient()
            resolved = ak_client.resolve_futures_code(text)
            if not resolved:
                return [], ""

            ak_symbol, ak_name = resolved
            bars_obj = await ak_client.get_futures_history(ak_symbol, days=60)
            if not bars_obj:
                return [], ""

            # 转为 dict 格式统一处理
            bars = [
                {
                    "date": str(b.trade_date),
                    "open": b.open, "high": b.high,
                    "low": b.low, "close": b.close,
                    "volume": b.volume, "oi": 0,
                    "change_pct": b.change_pct,
                }
                for b in bars_obj
            ]
            return bars, ak_symbol
        except Exception as e:
            logger.warning("[FuturesAnalysis] akshare 降级也失败: %s", e)
            return [], ""

    @staticmethod
    def _to_daily_bars(bars_raw: list[dict]) -> list[StockDailyBar]:
        return [
            StockDailyBar(
                code=b.get("code", ""),
                trade_date=b["date"],
                open=b["open"], high=b["high"],
                low=b["low"], close=b["close"],
                volume=b["volume"],
                amount=b.get("amount", 0),
                change_pct=b["change_pct"],
            )
            for b in bars_raw
        ]

    @staticmethod
    def _build_context(
        name: str, code: str, latest: dict, bars: list[dict], tech,
    ) -> str:
        """组装期货分析上下文"""
        lines = [f"请对以下期货品种进行技术分析，给出操作建议：\n"]

        lines.append(f"【品种】{name}（{code}）")
        oi_text = f" 持仓量{latest.get('oi', 0):.0f}手" if latest.get("oi") else ""
        lines.append(
            f"【最新】收盘{latest['close']} 涨跌{latest['change_pct']:+.2f}%{oi_text}"
        )

        if tech:
            lines.append(f"\n【技术指标】")
            lines.append(f"均线：MA5={tech.ma5} MA10={tech.ma10} MA20={tech.ma20}")
            lines.append(
                f"MACD：DIF={tech.macd:.3f} DEA={tech.macd_signal:.3f} "
                f"柱={tech.macd_hist:+.3f}"
            )
            lines.append(f"RSI(14)：{tech.rsi_14:.1f}")
            lines.append(f"趋势：{tech.trend}  支撑：{tech.support}  压力：{tech.resistance}")

        lines.append(f"\n【近10日K线】")
        for b in bars[-10:]:
            oi_part = f" 持仓{b.get('oi', 0):.0f}" if b.get("oi") else ""
            lines.append(
                f"  {b['date']}: 开{b['open']} 高{b['high']} 低{b['low']} "
                f"收{b['close']} 量{b['volume']:.0f}{oi_part} "
                f"涨跌{b['change_pct']:+.2f}%"
            )

        lines.append(
            f"\n请分析：1.趋势方向 2.关键价位 "
            f"3.操作建议（做多/做空/观望）4.止损位"
        )
        return "\n".join(lines)
