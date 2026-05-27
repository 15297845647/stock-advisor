"""Prompt 模板加载与构造"""

from pathlib import Path

from domain.models.stock import FundFlow, StockDailyBar, StockQuote
from domain.models.user_context import UserContext
from domain.stock_analyzer import TechnicalSnapshot

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_template(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def build_system_prompt() -> str:
    return _load_template("system.txt")


def build_analysis_prompt(
    quote: StockQuote,
    tech: TechnicalSnapshot,
    fund_flows: list[FundFlow],
    bars: list[StockDailyBar],
) -> str:
    """构造技术分析 prompt"""
    template = _load_template("analysis.txt")

    ma60_line = f"MA60={tech.ma60}" if tech.ma60 else "MA60=数据不足"

    # 资金流向文本
    flow_lines = []
    for f in fund_flows:
        flow_lines.append(
            f"  {f.trade_date}: 主力净流入{f.main_net_inflow/1e4:.0f}万 "
            f"超大单{f.super_large_net/1e4:.0f}万 大单{f.large_net/1e4:.0f}万"
        )
    fund_flow_text = "\n".join(flow_lines) if flow_lines else "暂无数据"

    # 近10日K线
    recent = bars[-10:] if len(bars) >= 10 else bars
    kline_lines = []
    for b in recent:
        kline_lines.append(
            f"  {b.trade_date}: 开{b.open} 高{b.high} 低{b.low} "
            f"收{b.close} 量{b.volume:.0f} 涨跌{b.change_pct:+.2f}%"
        )
    kline_summary = "\n".join(kline_lines)

    return template.format(
        stock_code=quote.code,
        stock_name=quote.name,
        price=quote.price,
        change_pct=quote.change_pct,
        ma5=tech.ma5, ma10=tech.ma10, ma20=tech.ma20,
        ma60_line=ma60_line,
        macd=tech.macd, macd_signal=tech.macd_signal, macd_hist=tech.macd_hist,
        rsi_14=tech.rsi_14,
        kdj_k=tech.kdj_k, kdj_d=tech.kdj_d, kdj_j=tech.kdj_j,
        boll_upper=tech.boll_upper, boll_mid=tech.boll_mid, boll_lower=tech.boll_lower,
        trend=tech.trend,
        support=tech.support, resistance=tech.resistance,
        fund_flow_text=fund_flow_text,
        kline_summary=kline_summary,
    )


def build_chat_prompt(ctx: UserContext, user_message: str) -> str:
    """构造对话 prompt（注入用户上下文）"""
    template = _load_template("chat.txt")

    risk_map = {"conservative": "保守型", "moderate": "稳健型", "aggressive": "激进型"}
    style_map = {"day": "短线", "swing": "波段", "position": "中长线"}

    memories_text = "\n".join(f"- {m}" for m in ctx.memories) if ctx.memories else "暂无记忆"
    watchlist_text = ", ".join(ctx.watchlist) if ctx.watchlist else "暂无关注"

    chat_lines = []
    for msg in ctx.recent_chat:
        role_label = "用户" if msg.role == "user" else "助手"
        chat_lines.append(f"{role_label}: {msg.content}")
    chat_history_text = "\n".join(chat_lines) if chat_lines else "无历史对话"

    return template.format(
        risk_level=risk_map.get(ctx.profile.risk_level, ctx.profile.risk_level),
        trade_style=style_map.get(ctx.profile.trade_style, ctx.profile.trade_style),
        memories_text=memories_text,
        watchlist_text=watchlist_text,
        chat_history_text=chat_history_text,
        user_message=user_message,
    )
