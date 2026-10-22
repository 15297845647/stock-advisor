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
from infrastructure.exchange_calendar import trading_dates, VERSION, SOURCES


class ResearchSource:
    async def _call(self, fn, **kwargs):
        return await asyncio.wait_for(asyncio.to_thread(partial(fn, **kwargs)), 20)

    async def collect(self, code, as_of):
        fetched=datetime.now(SH)
        if as_of.astimezone(SH).date()!=fetched.date():
            raise ValueError('historical live-source replay is unsupported; use frozen datasets')
        import akshare as ak
        errors=[]
        async def optional(fn, **kwargs):
            try:
                return await self._call(fn, **kwargs)
            except Exception as exc:
                errors.append(fn.__name__+':'+type(exc).__name__)
                return None
        end = as_of.astimezone(SH).date()
        calendar=trading_dates(end.isoformat())
        results = await asyncio.gather(
            optional(ak.stock_zh_a_hist, symbol=code, period='daily', adjust='qfq', timeout=15,
                     start_date=(end-timedelta(days=500)).strftime('%Y%m%d'), end_date=end.strftime('%Y%m%d')),
            optional(ak.stock_individual_info_em, symbol=code),
            optional(ak.stock_financial_abstract, symbol=code),
            optional(ak.stock_news_em, symbol=code),
            optional(ak.stock_zh_a_hist, symbol=code, period='daily', adjust='', timeout=15,
                     start_date=(end-timedelta(days=10)).strftime('%Y%m%d'), end_date=end.strftime('%Y%m%d')),
            optional(ak.stock_individual_spot_xq, symbol=('SH' if code.startswith('6') else 'BJ' if code.startswith(('4','8','9')) else 'SZ')+code, timeout=15),
        )
        history, info, finance, news, raw, spot = results
        data = {'calendar_dates':[], 'bars':[], 'news':[], 'fundamentals':{}, 'sources':{
            'bars':'akshare.stock_zh_a_hist/qfq', 'quote':'akshare.stock_zh_a_hist/raw_completed_close',
            'fundamentals':'akshare.stock_financial_abstract'}}
        data['fetched_at']=fetched.isoformat()
        data['source_errors']=errors
        data['calendar_version']=VERSION
        data['calendar_sources']=SOURCES
        data['calendar_dates']=calendar
        if spot is not None and not spot.empty:
            fields=dict(zip(spot['item'].astype(str),spot['value']))
            try:
                observed=datetime.fromisoformat(str(fields['时间']))
                if observed.tzinfo is None: observed=observed.replace(tzinfo=SH)
                data['quote']={'price':float(fields['现价']),'name':str(fields.get('名称',code)),
                    'kind':'realtime','observed_at':observed.isoformat()}
                data['sources']['quote']='akshare.stock_individual_spot_xq'
            except (KeyError,ValueError,TypeError):
                errors.append('stock_individual_spot_xq:InvalidSchema')
        if raw is not None and not raw.empty:
            completed=raw[raw['日期'].astype(str).str[:10] < end.isoformat()] if fetched.hour<15 else raw
            if not completed.empty:
                row=completed.iloc[-1]
                data['raw_close']={'price':float(row['收盘']),'trade_date':str(row['日期'])[:10]}
        if history is not None:
            for _, row in history.iterrows():
                numbers = {k:float(row[c]) for k,c in [('open','开盘'),('high','最高'),('low','最低'),
                         ('close','收盘'),('volume','成交量'),('amount','成交额')]}
                if not all(math.isfinite(v) for v in numbers.values()):
                    continue
                numbers['volume'] *= 100  # Eastmoney daily reports lots; contract uses shares.
                data['bars'].append(dict(numbers, trade_date=str(row['日期'])[:10],
                    adjustment='qfq', volume_unit='share', amount_unit='CNY'))
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
        return data
