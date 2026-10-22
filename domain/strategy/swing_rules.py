"""Deterministic daily swing screens; thresholds are hypotheses, not proven alpha."""
from pathlib import Path
from statistics import mean
from uuid import uuid4
import yaml
from domain.data_quality import validate_snapshot
from domain.models.research import ResearchDecision, digest


def load_config():
    path=Path(__file__).resolve().parents[2]/'config/swing_strategies.yaml'
    return yaml.safe_load(path.read_text())


def evaluate_strategy(snapshot, strategy_id, config):
    if strategy_id not in ('pullback','breakout'):
        raise ValueError('unknown strategy')
    d=ResearchDecision(str(uuid4()),'',snapshot.stock_code,horizon_days=config['horizon_days'])
    d.metadata={'strategy':strategy_id,'config_hash':digest(config),'signal_at':snapshot.as_of.isoformat(),
                'data_as_of':snapshot.as_of.isoformat()}
    errors=validate_snapshot(snapshot)
    if errors:
        d.status='blocked'; d.risk_flags=errors
        return d
    bars=snapshot.bars
    if len(bars)<config['min_bars']:
        d.status='blocked'; d.risk_flags=['insufficient_history']; return d
    closes=[b['close'] for b in bars]
    ma20,ma60=mean(closes[-20:]),mean(closes[-60:])
    price=closes[-1]
    status=snapshot.security_status
    checks={'非ST':status.get('is_st') is False, '未停牌':status.get('suspended') is False,
            '有成交':bars[-1]['volume']>0,
            '流动性':mean(b['amount'] for b in bars[-20:])>=config['min_amount'],
            'MA20高于MA60':ma20>ma60}
    if strategy_id=='pullback':
        volume_base=mean(b['volume'] for b in bars[-25:-5])
        volume_ratio=mean(b['volume'] for b in bars[-5:])/volume_base if volume_base>0 else None
        distance=price/ma20-1
        checks.update({'MA20上行':ma20>mean(closes[-25:-5]),
                       '未跌破均线且回踩距离合格':0<=distance<=config['pullback']['max_distance'],
                       '回踩缩量':volume_ratio is not None and volume_ratio<=config['pullback']['max_volume_ratio']})
    else:
        resistance=max(b['high'] for b in bars[-21:-1])
        distance=price/resistance-1
        volume_base=mean(b['volume'] for b in bars[-21:-1])
        volume_ratio=bars[-1]['volume']/volume_base if volume_base>0 else None
        checks.update({'突破幅度合格':config['breakout']['min_breakout']<=distance<=config['breakout']['max_breakout'],
                       '突破放量':volume_ratio is not None and volume_ratio>=config['breakout']['min_volume_ratio']})
    atr=mean(max(b['high']-b['low'],abs(b['high']-p['close']),abs(b['low']-p['close']))
             for p,b in zip(bars[-15:-1],bars[-14:]))
    support=min(b['low'] for b in bars[-20:])
    stop=max(support,price-2*atr)
    # Convert signal-scale risk level to the contemporaneous unadjusted quote.
    stop *= snapshot.quote['price']/price
    d.metadata.update(checks=checks,ma20=ma20,ma60=ma60,atr14=atr,support=support,
                      volume_ratio=volume_ratio,distance=distance,signal_price=price,
                      signal_date=bars[-1]['trade_date'],stop_loss=stop if 0<stop<snapshot.quote['price'] else None,
                      adjustment=bars[-1]['adjustment'])
    d.status='eligible' if all(checks.values()) else 'observe'
    d.rule_score=round(sum(checks.values())/len(checks)*100,2)
    d.trigger_conditions=[k for k,v in checks.items() if v]
    d.invalidation_conditions=[k+'不满足' for k,v in checks.items() if not v]
    d.invalidation_conditions.append(f'收盘跌破MA20 ({ma20:.3f})，重新评估')
    d.supporting_evidence_ids=[e.evidence_id for e in snapshot.evidence if e.kind in ('bars','daily')]
    if status.get('is_st') is None or status.get('suspended') is None:
        d.risk_flags.append('security_status_unknown')
    if snapshot.market_session!='closed':
        d.status='observe'; d.risk_flags.append('daily_strategy_wait_for_close')
    return d
