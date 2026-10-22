"""Conservative daily-bar long-only evaluation, distinct from direction accuracy.

Actual exchange limit prices must come from dated metadata, never a universal 10% rule.
Untradeable or unsupported corporate-action samples do not manufacture returns.
"""
from datetime import datetime
from statistics import mean
from domain.data_quality import SH, completed_session, finite, parse_time
from domain.models.research import EvaluationResult


def dated_fees(config, day):
    rows=[r for r in config.get('fee_schedule',[]) if r['effective_from']<=day
          and (not r.get('effective_to') or day<=r['effective_to'])]
    if not rows: return None
    r=max(rows,key=lambda r:r['effective_from'])
    if not all(finite(r.get(k)) for k in ('commission','min_commission','transfer_fee','stamp_tax_sell')):
        return None
    return r


def fee(value, rules, sell=False):
    return max(rules['min_commission'],value*rules['commission'])+value*(rules['transfer_fee']+(rules['stamp_tax_sell'] if sell else 0))


def evaluate_run(run, bars, horizon_days, execution_config):
    cfg=execution_config
    assumptions={'entry':'next_session_open_no_chasing','t_plus_one':True,'double_touch':'stop_first',
        'scope':'single_signal_equal_capital_not_portfolio','slippage_bps':cfg.get('slippage_bps'),
        'costs':'configured_scenario_not_actual_account','benchmark':cfg.get('benchmark','CSI300')}
    def result(status,reason=None,metrics=None):
        return EvaluationResult(run.run_id,horizon_days,status,metrics or {},assumptions,reason)
    if horizon_days not in (5,10,20): return result('unavailable','unsupported_horizon')
    if run.decision.status!='eligible': return result('unavailable','no_long_entry')
    # The initial execution scenario only models ordinary Shanghai/Shenzhen A shares.
    if not run.decision.stock_code.startswith(('600','601','603','605','000','001','002','003','300','301')):
        return result('unavailable','board_execution_unsupported')
    as_of=parse_time(cfg.get('as_of'))
    if not as_of or run.created_at.tzinfo is None: return result('unavailable','time_unknown')
    signal_day=run.created_at.astimezone(SH).date().isoformat()
    calendar=sorted(set(cfg.get('calendar_dates',[])))
    if not calendar: return result('unavailable','calendar_unknown')
    sessions=[d for d in calendar if d>signal_day][:horizon_days]
    last=completed_session(as_of,calendar)
    if len(sessions)<horizon_days or not last or sessions[-1]>last: return result('pending')
    by_date={b['trade_date']:b for b in bars}
    if len(by_date)!=len(bars): return result('unavailable','duplicate_price')
    if any(d not in by_date for d in sessions): return result('unavailable','missing_price')
    window=[by_date[d] for d in sessions]
    for b in window:
        if not all(finite(b.get(k),True) for k in ('open','high','low','close')):
            return result('unavailable','invalid_price')
        if not b['low']<=min(b['open'],b['close'])<=max(b['open'],b['close'])<=b['high']:
            return result('unavailable','invalid_price')
        if not finite(b.get('volume')) or b.get('adjustment')!='raw': return result('unavailable','price_basis_unknown')
        if b.get('corporate_action') is not False: return result('unavailable','corporate_action_unsupported')
        if not all(finite(b.get(k),True) for k in ('limit_up','limit_down')):
            return result('unavailable','limits_unknown')
        if b['limit_down']>=b['limit_up']: return result('unavailable','limits_invalid')
    entry_day=sessions[0]
    buy_rules=dated_fees(cfg,entry_day)
    if not buy_rules: return result('unavailable','fees_unknown')
    capital=cfg.get('capital'); lot=cfg.get('lot_size'); slip=cfg.get('slippage_bps')
    if not finite(capital,True) or not isinstance(lot,int) or lot<=0 or not finite(slip) or slip>=10000:
        return result('unavailable','invalid_execution_config')
    first=window[0]
    if first['volume']==0 or first.get('suspended') or first['open']>=first['limit_up']:
        return result('unavailable','entry_unfilled')
    entry=first['open']*(1+slip/10000)
    if entry>first['high'] or entry>=first['limit_up']: return result('unavailable','entry_unfilled')
    shares=int(capital/entry)//lot*lot
    while shares>0 and shares*entry+fee(shares*entry,buy_rules)>capital: shares-=lot
    if shares<=0: return result('unavailable','insufficient_capital')
    stop=run.decision.metadata.get('stop_loss')
    target=run.decision.metadata.get('target_price')
    if stop is not None and (not finite(stop,True) or stop>=entry): return result('unavailable','invalid_stop')
    if target is not None and (not finite(target,True) or target<=entry): return result('unavailable','invalid_target')
    exit_price=None; exit_day=None; reason=None; pending_stop=False
    peak=capital; max_dd=0
    entry_cost=shares*entry+fee(shares*entry,buy_rules)
    def mark_close(b):
        nonlocal peak,max_dd
        value=capital-entry_cost+shares*b['close']
        peak=max(peak,value);max_dd=max(max_dd,(peak-value)/peak)
    for i,b in enumerate(window):
        if i==0:
            mark_close(b)
            continue  # no sale on purchase day, even if a level was touched
        stop_hit=stop is not None and b['low']<=stop
        target_hit=target is not None and b['high']>=target
        if b['volume']==0 or b.get('suspended') or b['high']<=b['limit_down']:
            pending_stop=pending_stop or stop_hit
            mark_close(b)
            continue
        if pending_stop:
            exit_price=b['open'];reason='deferred_stop'
        elif stop_hit:
            exit_price=min(b['open'],stop);reason='stop'
        elif target_hit:
            exit_price=target;reason='target'  # no optimistic gap-up fill
        elif i==len(window)-1:
            exit_price=b['close'];reason='window_end'
        if exit_price is not None:
            slipped=exit_price*(1-slip/10000)
            if slipped<b['low'] or slipped<=b['limit_down']:
                exit_price=None; pending_stop=pending_stop or stop_hit; mark_close(b); continue
            exit_price=slipped;exit_day=b['trade_date'];break
        mark_close(b)
    if exit_price is None: return result('unavailable','exit_unfilled')
    sell_rules=dated_fees(cfg,exit_day)
    if not sell_rules: return result('unavailable','fees_unknown')
    proceeds=shares*exit_price-fee(shares*exit_price,sell_rules,True)
    net=(proceeds-entry_cost)/capital*100
    final_equity=capital-entry_cost+proceeds
    max_dd=max(max_dd,(peak-final_equity)/peak)
    benchmark={b['trade_date']:b for b in cfg.get('benchmark_bars',[])}
    # Compare same horizon dates, leaving strategy cash flat after early exit.
    bstart=benchmark.get(entry_day,{}).get('open'); bend=benchmark.get(sessions[-1],{}).get('close')
    breturn=(bend/bstart-1)*100 if finite(bstart,True) and finite(bend,True) else None
    metrics={'entry_date':entry_day,'entry_price':entry,'exit_date':exit_day,'exit_price':exit_price,
        'exit_reason':reason,'shares':shares,'net_return_pct':net,'benchmark_return_pct':breturn,
        'excess_return_pct':net-breturn if breturn is not None else None,'max_drawdown_pct':max_dd*100,
        'turnover':(shares*entry+shares*exit_price)/capital,'fees':fee(shares*entry,buy_rules)+fee(shares*exit_price,sell_rules,True),
        'direction_correct':window[-1]['close']>first['open'],'window_days':horizon_days}
    if breturn is None: assumptions['benchmark_limitation']='missing_benchmark'
    return result('evaluated',metrics=metrics)


def summarize_evaluations(results):
    evaluated=[r for r in results if r.status=='evaluated']
    return {'total':len(results),'evaluated':len(evaluated),
        'pending':sum(r.status=='pending' for r in results),
        'unavailable':sum(r.status=='unavailable' for r in results),
        'mean_net_return_pct':mean(r.metrics['net_return_pct'] for r in evaluated) if evaluated else None,
        'win_rate':mean(r.metrics['net_return_pct']>0 for r in evaluated) if evaluated else None,
        'direction_accuracy':mean(r.metrics['direction_correct'] for r in evaluated) if evaluated else None,
        'scope':'single_signal_statistics_not_portfolio_backtest'}
