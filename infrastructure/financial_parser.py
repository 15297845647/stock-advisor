"""Parse indicator-by-period AKShare financial abstract without zero filling."""
import math

FIELDS = {'revenue': ('营业收入', '营业总收入'), 'net_profit': ('净利润',),
          'roe': ('净资产收益率(ROE)', '净资产收益率'), 'eps': ('基本每股收益', '每股收益'),
          'bvps': ('每股净资产',), 'revenue_growth': ('营业总收入增长率', '营业收入同比增长'),
          'profit_growth': ('归母净利润增长率', '净利润同比增长')}


def parse_financial_abstract(rows):
    result = dict.fromkeys(FIELDS)
    periods = sorted({str(k) for row in rows for k in row if str(k).isdigit() and len(str(k)) == 8})
    result.update(report_period=periods[-1] if periods else None, available_at=None)
    if not periods:
        return result
    for row in rows:
        indicator = str(row.get('指标', ''))
        for key, aliases in FIELDS.items():
            if indicator in aliases:
                try:
                    number = float(row.get(periods[-1]))
                    result[key] = number if math.isfinite(number) else None
                except (TypeError, ValueError):
                    pass
    return result
