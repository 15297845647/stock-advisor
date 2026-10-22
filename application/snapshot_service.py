"""Collect once; data gates run before any report-cache or model lookup."""
import asyncio
from datetime import datetime, time, timedelta
from domain.data_quality import SH, completed_session, validate_snapshot, parse_time
from domain.models.research import AnalysisSnapshot, EvidenceItem


class SnapshotService:
    def __init__(self, source=None):
        if source is None:
            from infrastructure.research_source import ResearchSource
            source = ResearchSource()
        self.source = source

    async def collect(self, stock_code: str, as_of: datetime) -> AnalysisSnapshot:
        if len(stock_code) != 6 or not stock_code.isdigit():
            raise ValueError('expected six-digit A-share code')
        if as_of.tzinfo is None:
            raise ValueError('as_of requires timezone')
        payload = await asyncio.wait_for(self.source.collect(stock_code, as_of), timeout=90)
        calendar = payload.get('calendar_dates', [])
        last = completed_session(as_of, calendar)
        local = as_of.astimezone(SH)
        today = local.date().isoformat()
        session = 'closed'
        if today in calendar and time(9,30) <= local.time() < time(15):
            session = 'lunch' if time(11,30) <= local.time() < time(13) else 'intraday'
        bars = [b for b in payload.get('bars', []) if last and b['trade_date'] <= last]
        quote = payload.get('quote', {})
        quote_source=payload.get('sources',{}).get('quote','unknown')
        # A known completed daily close may be used only outside the trading session.
        if session == 'closed' and bars and bars[-1]['adjustment'] == 'raw':
            quote = dict(price=bars[-1]['close'], kind='close', name=payload.get('name', stock_code),
                         observed_at=bars[-1]['trade_date']+'T15:00:00+08:00')
            quote_source=payload.get('sources',{}).get('bars','unknown')
        elif session == 'closed' and payload.get('raw_close'):
            raw=payload['raw_close']
            quote=dict(price=raw['price'],kind='close',name=payload.get('name',stock_code),
                       observed_at=raw['trade_date']+'T15:00:00+08:00')
            quote_source=raw.get('source','akshare.stock_zh_a_hist/raw_completed_close')
        evidence = []
        if payload.get('calendar_version'):
            evidence.append(EvidenceItem('calendar','calendar',payload['calendar_version'],
                payload.get('fetched_at',as_of.isoformat()),{'sources':payload.get('calendar_sources',[])}))
        for key in ('quote', 'bars', 'fundamentals', 'fund_flows'):
            value = quote if key == 'quote' else bars if key == 'bars' else payload.get(key, {})
            if value:
                evidence.append(EvidenceItem(key, key, quote_source if key=='quote' else payload.get('sources', {}).get(key, 'unknown'),
                    payload.get('fetched_at',as_of.isoformat()), {'data':value}, observed_at=quote.get('observed_at') if key=='quote' else None))
        news = []
        for i, item in enumerate(payload.get('news', [])):
            stamp = parse_time(item.get('published_at'))
            if stamp is None or stamp > as_of:
                continue
            item = dict(item, is_catalyst=as_of-stamp <= timedelta(hours=72))
            news.append(item)
            evidence.append(EvidenceItem(f'news-{i}', 'news', item.get('source','unknown'), as_of.isoformat(),
                item, source_url=item.get('url'), published_at=stamp.isoformat(),
                quality_flags=[] if item['is_catalyst'] else ['background_only']))
        s = AnalysisSnapshot('', stock_code, as_of, session, quote, bars,
            fundamentals=payload.get('fundamentals', {}), fund_flows=payload.get('fund_flows', []),
            news=news, evidence=evidence, calendar_dates=calendar,
            security_status=payload.get('security_status', {}))
        s.missing_fields = [key for key in ('fundamentals','news','fund_flows') if not getattr(s,key)]
        if s.fundamentals and not s.fundamentals.get('available_at'):
            s.missing_fields.append('financial_publication_time_unknown')
        errors = validate_snapshot(s)
        s.quality_status = 'blocked' if errors else 'partial' if s.missing_fields else 'ready'
        s.missing_fields.extend(errors)
        s.missing_fields.extend('source_error:'+e for e in payload.get('source_errors',[]))
        return s.seal()
