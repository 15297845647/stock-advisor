"""Prompt 模板加载与构造"""

from pathlib import Path

from domain.models.stock import FundFlow, StockDailyBar, StockFundamental, StockNews, StockQuote
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
    news: list[StockNews] | None = None,
    fundamentals: StockFundamental | None = None,
) -> str:
    """构造综合分析 prompt（技术面 + 基本面 + 资金面 + 消息面）"""
    template = _load_template("analysis.txt")

    ma60_line = f"MA60={tech.ma60}" if tech.ma60 else "MA60=数据不足"

    flow_lines = []
    for f in fund_flows:
        flow_lines.append(
            f"  {f.trade_date}: 主力净流入{f.main_net_inflow/1e4:.0f}万 "
            f"超大单{f.super_large_net/1e4:.0f}万 大单{f.large_net/1e4:.0f}万"
        )
    fund_flow_text = "\n".join(flow_lines) if flow_lines else "暂无数据"

    recent = bars[-10:] if len(bars) >= 10 else bars
    kline_lines = []
    for b in recent:
        kline_lines.append(
            f"  {b.trade_date}: 开{b.open} 高{b.high} 低{b.low} "
            f"收{b.close} 量{b.volume:.0f} 涨跌{b.change_pct:+.2f}%"
        )
    kline_summary = "\n".join(kline_lines)

    # 基本面文本
    fund_lines = []
    if fundamentals:
        if fundamentals.industry:
            fund_lines.append(f"  行业：{fundamentals.industry}")
        if fundamentals.total_market_cap:
            fund_lines.append(f"  总市值：{fundamentals.total_market_cap/1e8:.1f}亿")
        if fundamentals.pe_ratio:
            fund_lines.append(f"  PE(市盈率)：{fundamentals.pe_ratio:.1f}")
        if fundamentals.pb_ratio:
            fund_lines.append(f"  PB(市净率)：{fundamentals.pb_ratio:.2f}")
        if fundamentals.roe:
            fund_lines.append(f"  ROE(净资产收益率)：{fundamentals.roe:.1f}%")
        if fundamentals.revenue:
            fund_lines.append(f"  营收：{fundamentals.revenue/1e8:.1f}亿 同比{fundamentals.revenue_growth:+.1f}%")
        if fundamentals.net_profit:
            fund_lines.append(f"  净利润：{fundamentals.net_profit/1e8:.1f}亿 同比{fundamentals.profit_growth:+.1f}%")
        if fundamentals.eps:
            fund_lines.append(f"  每股收益：{fundamentals.eps:.2f}元")
        if fundamentals.debt_ratio:
            fund_lines.append(f"  资产负债率：{fundamentals.debt_ratio:.1f}%")
        if fundamentals.report_period:
            fund_lines.append(f"  最新报告期：{fundamentals.report_period}")
    fundamental_text = "\n".join(fund_lines) if fund_lines else "暂无基本面数据"

    # 新闻/公告文本
    news_lines = []
    if news:
        for n in news[:15]:
            tag = "📰" if n.news_type == "news" else "📋"
            news_lines.append(f"  {tag} [{n.time}] {n.title}（{n.source}）")
    news_text = "\n".join(news_lines) if news_lines else "暂无近期新闻"

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
        fundamental_text=fundamental_text,
        fund_flow_text=fund_flow_text,
        kline_summary=kline_summary,
        news_text=news_text,
    )


def build_recommend_prompt(
    ctx: UserContext,
    rank_data: list[dict],
    sector_data: list[dict],
) -> str:
    """构造选股推荐 prompt"""
    template = _load_template("recommend.txt")

    risk_map = {"conservative": "保守型", "moderate": "稳健型", "aggressive": "激进型"}
    style_map = {"day": "短线", "swing": "波段", "position": "中长线"}

    rank_lines = []
    for s in rank_data:
        rank_lines.append(
            f"  {s['code']} {s['name']}  "
            f"价格{s['price']}  涨跌{s['change_pct']:+.2f}%  "
            f"换手{s.get('turnover', 0):.1f}%"
        )
    rank_text = "\n".join(rank_lines) if rank_lines else "数据暂不可用"

    sector_lines = []
    for sec in sector_data:
        flow = sec.get("main_net_inflow", 0)
        sector_lines.append(
            f"  {sec['name']}  涨跌{sec['change_pct']:+.2f}%  "
            f"主力净流入{flow/1e8:.1f}亿"
        )
    sector_text = "\n".join(sector_lines) if sector_lines else "数据暂不可用"

    return template.format(
        risk_level=risk_map.get(ctx.profile.risk_level, ctx.profile.risk_level),
        trade_style=style_map.get(ctx.profile.trade_style, ctx.profile.trade_style),
        rank_data=rank_text,
        sector_data=sector_text,
    )


def build_chat_prompt(ctx: UserContext, user_message: str) -> str:
    """构造对话 prompt（注入用户上下文）"""
    template = _load_template("chat.txt")

    risk_map = {"conservative": "保守型", "moderate": "稳健型", "aggressive": "激进型"}
    style_map = {"day": "短线", "swing": "波段", "position": "中长线"}

    memories_text = "\n".join(f"- {m}" for m in ctx.memories) if ctx.memories else "暂无记忆"

    chat_lines = []
    for msg in ctx.recent_chat:
        role_label = "用户" if msg.role == "user" else "助手"
        chat_lines.append(f"{role_label}: {msg.content}")
    chat_history_text = "\n".join(chat_lines) if chat_lines else "无历史对话"

    return template.format(
        risk_level=risk_map.get(ctx.profile.risk_level, ctx.profile.risk_level),
        trade_style=style_map.get(ctx.profile.trade_style, ctx.profile.trade_style),
        memories_text=memories_text,
        chat_history_text=chat_history_text,
        user_message=user_message,
    )
