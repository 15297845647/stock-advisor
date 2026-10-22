"""Replay caller-supplied point-in-time datasets; never call today's model for history."""
from uuid import uuid4
from domain.models.research import AnalysisSnapshot, AnalysisRun, digest
from domain.data_quality import parse_time,validate_snapshot
from domain.strategy.swing_rules import evaluate_strategy,load_config
from domain.execution_rules import evaluate_run,summarize_evaluations
from domain.report_renderer import render_research_report
from repository.research_repository import ResearchRepository
from application.evaluation_service import EvaluationService


class HistoricalResearchService:
    def __init__(self,repository=None):
        self.repository=repository or ResearchRepository()

    async def replay(self,dataset,strategy_id='pullback'):
        config=load_config()
        split=parse_time(dataset.get('test_start')); frozen=parse_time(dataset.get('config_frozen_at'))
        as_of=parse_time(dataset.get('as_of'))
        if not split or not frozen or not as_of or frozen>split or as_of<split:
            raise ValueError('invalid parameter freeze/test split')
        if dataset.get('config_hash')!=digest(config): raise ValueError('strategy configuration hash differs')
        if not dataset.get('universe_description') or not dataset.get('survivorship_bias'):
            raise ValueError('universe coverage and survivorship disclosure required')
        prepared=[]
        for row in dataset['records']:
            s=AnalysisSnapshot.from_dict(row['snapshot'])
            if s.as_of>as_of or s.market_session!='closed' or validate_snapshot(s):
                raise ValueError('invalid historical snapshot')
            if not s.evidence: raise ValueError('available_at evidence required')
            for e in s.evidence:
                known=parse_time(e.available_at)
                if not known or known>s.as_of: raise ValueError('evidence available_at is missing or future')
            prepared.append((s,row['execution']))
        # Validate the entire dataset before creating any research rows.
        prepared.sort(key=lambda item:(item[0].as_of,item[0].stock_code))
        results={'in_sample':{str(h):[] for h in (5,10,20)},'out_of_sample':{str(h):[] for h in (5,10,20)}}
        execution=EvaluationService(self.repository).config
        for s,data in prepared:
            s.seal(); decision=evaluate_strategy(s,strategy_id,config)
            rid=str(uuid4());decision.run_id=rid
            partition='in_sample' if s.as_of<split else 'out_of_sample'
            decision.metadata.update(partition=partition,dataset_hash=digest(dataset))
            run=AnalysisRun(rid,s.snapshot_id,s.as_of,'quick',config['version']+':'+digest(config)[:12],
                'evidence-v1','none','complete',decision,group='historical_rules',stages=['frozen_dataset','rules'])
            run.report=render_research_report(run,s)
            await self.repository.save_run(run,s)
            cfg=dict(execution,**{k:v for k,v in data.items() if k!='bars'},as_of=as_of.isoformat())
            for h in (5,10,20):
                result=evaluate_run(run,data['bars'],h,cfg)
                result.assumptions.update(partition=partition,input_hash=digest(data),group='historical_rules')
                await self.repository.save_evaluation(result,digest(execution))
                results[partition][str(h)].append(result)
        return {**{p:{h:summarize_evaluations(v) for h,v in windows.items()} for p,windows in results.items()},
            'dataset_hash':digest(dataset),'config_hash':digest(config),'model_calls':0,
            'universe_description':dataset['universe_description'],'survivorship_bias':dataset['survivorship_bias'],
            'limitation':'availability provenance is supplied by dataset owner and requires audit'}
