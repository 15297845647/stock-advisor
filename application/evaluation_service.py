from datetime import datetime
from pathlib import Path
from dataclasses import asdict
import yaml
from domain.models.research import digest, EvaluationResult
from domain.execution_rules import evaluate_run
from repository.research_repository import ResearchRepository
from domain.data_quality import SH
from infrastructure.exchange_calendar import trading_dates


class EvaluationService:
    def __init__(self,repository=None,loader=None,config=None):
        self.repository=repository or ResearchRepository()
        self.loader=loader
        self.config=config or yaml.safe_load((Path(__file__).resolve().parents[1]/'config/execution_rules.yaml').read_text())

    async def evaluate_mature_runs(self,as_of: datetime,horizons: tuple[int,...]=(5,10,20)):
        if as_of.tzinfo is None: raise ValueError('as_of requires timezone')
        config_hash=digest(self.config)
        results=[]
        for run in await self.repository.list_runs(10000):
            if run.created_at>as_of: continue
            if run.group=='historical_rules': continue
            remaining=[]
            try:
                calendar=trading_dates(as_of.astimezone(SH).date().isoformat())
            except ValueError:
                calendar=[]
            for horizon in horizons:
                preliminary=evaluate_run(run,[],horizon,dict(self.config,
                    calendar_dates=calendar,as_of=as_of.isoformat()))
                if preliminary.status=='pending' or preliminary.exclusion_reason in ('no_long_entry','board_execution_unsupported','unsupported_horizon'):
                    preliminary.assumptions['group']=run.group
                    await self.repository.save_evaluation(preliminary,config_hash)
                    results.append(preliminary)
                else:
                    remaining.append(horizon)
            if not remaining: continue
            try:
                if self.loader is None:
                    from infrastructure.evaluation_source import load_evaluation_data
                    data=await load_evaluation_data(run,as_of)
                else:
                    data=await self.loader(run,as_of)
                config=dict(self.config,**{k:v for k,v in data.items() if k!='bars'},as_of=as_of.isoformat())
                for horizon in remaining:
                    result=evaluate_run(run,data['bars'],horizon,config)
                    result.assumptions['input_hash']=digest(data)
                    result.assumptions['group']=run.group
                    await self.repository.save_evaluation(result,config_hash)
                    results.append(result)
            except Exception as exc:
                for horizon in remaining:
                    result=EvaluationResult(run.run_id,horizon,'unavailable',exclusion_reason='evaluation_source_failed:'+type(exc).__name__)
                    result.assumptions['group']=run.group
                    await self.repository.save_evaluation(result,config_hash)
                    results.append(result)
        return results
