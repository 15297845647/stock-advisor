"""A-share daily research adapter. Explicit source semantics, no fabricated timestamps.

Network access occurs only when collect() is called; tests inject an offline source.
Legacy source clients and their fallback behavior remain available to other features.
"""
import asyncio
from datetime import timedelta, datetime
from functools import partial
import math
from domain.data_quality import SH
from infrastructure.financial_parser import parse_financial_abstract


class ResearchSource:
    async def _call(self, fn, **kwargs):
        return await asyncio.wait_for(asyncio.to_thread(partial(fn, **kwargs)), 20)

    async def collect(self, code, as_of):
        import akshare as ak
        async def optional(fn, **kwargs):
            try:
                return await self._call(fn, **kwargs)
            except Exception:
                return None
        end = as_of.astimezone(SH).date()
        results = await asyncio.gather(
            optional(ak.tool_trade_date_hist_sina),
            optional(ak.stock_zh_a_hist, symbol=code, period='daily', adjust='',
                     start_date=(end-timedelta(days=500)).strftime('%Y%m%d'), end_date=end.strftime('%Y%m%d')),
            optional(ak.stock_individual_info_em, symbol=code),
            optional(ak.stock_financial_abstract, symbol=code),
            optional(ak.stock_news_em, symbol=code),
        )
        calendar, history, info, finance, news = results
        data = {'calendar_dates':[], 'bars':[], 'news':[], 'fundamentals':{}, 'sources':{
            'bars':'akshare.stock_zh_a_hist/raw', 'quote':'completed_daily_close',
            'fundamentals':'akshare.stock_financial_abstract'}}
        if calendar is not None:
            data['calendar_dates'] = sorted(str(d)[:10] for d in calendar['trade_date'])
        if history is not None:
            for _, row in history.iterrows():
                numbers = {k:float(row[c]) for k,c in [('open','开盘'),('high','最高'),('low','最低'),
                         ('close','收盘'),('volume','成交量'),('amount','成交额')]}
                if not all(math.isfinite(v) for v in numbers.values()):
                    continue
                numbers['volume'] *= 100  # Eastmoney daily reports lots; contract uses shares.
                data['bars'].append(dict(numbers, trade_date=str(row['日期'])[:10],
                    adjustment='raw', volume_unit='share', amount_unit='CNY'))
        if info is not None:
            fields = {str(r['item']):r['value'] for _,r in info.iterrows()}
            name = str(fields.get('股票简称',''))
            data['name'] = name or code
            # Fresh volume is a daily activity observation; missing name/volume is unknown.
            data['security_status'] = {'is_st':('ST' in name.upper()) if name else None,
                'suspended':data['bars'][-1]['volume']==0 if data['bars'] else None}
        if finance is not None:
            data['fundamentals'] = parse_financial_abstract(finance.to_dict('records'))
        if news is not None:
            for _,r in news.head(20).iterrows():
                try:
                    stamp = datetime.fromisoformat(str(r['发布时间'])).replace(tzinfo=SH)
                except (ValueError, KeyError):
                    continue
                data['news'].append({'title':str(r.get('新闻标题','')), 'source':str(r.get('文章来源','')),
                    'url':str(r.get('新闻链接','')), 'published_at':stamp.isoformat()})
        # Intraday quote timestamps are unavailable from this daily-only adapter.
        # SnapshotService deliberately blocks intraday decisions instead of dating a stale quote as now.
        return data
