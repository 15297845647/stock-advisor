"""Optional Tushare dated execution metadata. Missing permission/data fails explicitly."""
import asyncio
from datetime import timedelta
from domain.data_quality import SH


async def load_evaluation_data(run,as_of):
    import akshare as ak
    from infrastructure.data_source.tushare_source import _run_pro, _to_ts_code
    code=_to_ts_code(run.decision.stock_code)
    start=(run.created_at.astimezone(SH).date()-timedelta(days=10)).strftime('%Y%m%d')
    end=as_of.astimezone(SH).strftime('%Y%m%d')
    async def pro(name,**kwargs): return await asyncio.wait_for(_run_pro(name,**kwargs),30)
    daily,factors,limits,calendar=await asyncio.gather(
        pro('daily',ts_code=code,start_date=start,end_date=end),
        pro('adj_factor',ts_code=code,start_date=start,end_date=end),
        pro('stk_limit',ts_code=code,start_date=start,end_date=end),
        pro('trade_cal',exchange='SSE',start_date=start,end_date=end,is_open='1'))
    if any(x is None or x.empty for x in (daily,factors,limits,calendar)):
        raise ValueError('dated execution metadata unavailable')
    factor={str(r['trade_date']):float(r['adj_factor']) for _,r in factors.iterrows()}
    bound={str(r['trade_date']):r for _,r in limits.iterrows()}
    bars=[];previous=None
    def iso(d): return d[:4]+'-'+d[4:6]+'-'+d[6:8]
    for _,r in daily.sort_values('trade_date').iterrows():
        day=str(r['trade_date']);f=factor.get(day);lim=bound.get(day)
        bars.append({'trade_date':iso(day),'open':float(r['open']),'high':float(r['high']),
            'low':float(r['low']),'close':float(r['close']),'volume':float(r['vol'])*100,
            'adjustment':'raw','corporate_action':(f!=previous) if f is not None and previous is not None else None,
            'limit_up':float(lim['up_limit']) if lim is not None else None,
            'limit_down':float(lim['down_limit']) if lim is not None else None})
        previous=f
    benchmark=[]
    try:
        frame=await asyncio.wait_for(asyncio.to_thread(ak.index_zh_a_hist,symbol='000300',period='daily',start_date=start,end_date=end),20)
        benchmark=[{'trade_date':str(r['日期'])[:10],'open':float(r['开盘']),'close':float(r['收盘'])} for _,r in frame.iterrows()]
    except Exception:
        pass
    return {'bars':bars,'calendar_dates':[iso(str(d)) for d in calendar['cal_date']],
            'benchmark_bars':benchmark,'data_source':'tushare.daily+adj_factor+stk_limit'}
