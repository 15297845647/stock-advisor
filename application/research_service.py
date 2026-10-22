"""Version-2 research orchestration. Every conclusion binds to a saved snapshot."""
import asyncio
from datetime import datetime
from uuid import uuid4
from copy import deepcopy
from domain.data_quality import SH, validate_snapshot
from domain.models.research import AnalysisRun, AnalysisSnapshot, ResearchDecision, digest
from domain.strategy.swing_rules import evaluate_strategy, load_config
from domain.report_renderer import render_research_report
from application.snapshot_service import SnapshotService
from repository.research_repository import ResearchRepository


class ResearchService:
    def __init__(self, snapshots=None, repository=None, model=None):
        self.snapshots=snapshots or SnapshotService()
        self.repository=repository or ResearchRepository()
        self.model=model
        self._cache={}

    async def analyze_run(self, stock_code, depth='standard', force=False, *, as_of=None,
                          strategy_id='pullback', use_model=True, notifier=None):
        depth=getattr(depth,'value',depth)
        if depth not in ('quick','standard','deep'): raise ValueError('invalid depth')
        now=as_of or datetime.now(SH)
        if now.tzinfo is None: raise ValueError('as_of requires timezone')
        try:
            snapshot=await self.snapshots.collect(stock_code,now)
        except Exception as exc:
            snapshot=AnalysisSnapshot('',stock_code,now,'unknown',{},[],
                quality_status='blocked',missing_fields=['collection_failed:'+type(exc).__name__]).seal()
        errors=validate_snapshot(snapshot)
        config=load_config()
        if self.model is None and use_model and not errors:
            from infrastructure.research_model import ResearchModel
            self.model=ResearchModel()
        model_id=self.model.identity if use_model and self.model is not None else 'none'
        # seal only valid numeric inputs; blocked snapshots may retain a sanitized payload.
        snapshot.seal()
        key=digest([stock_code,depth,snapshot.content_hash,digest(config),strategy_id,'evidence-v1',model_id,use_model])
        if not force and not errors and key in self._cache:
            return deepcopy(self._cache[key])
        run_id=str(uuid4())
        decision=evaluate_strategy(snapshot,strategy_id,config)
        decision.run_id=run_id
        decision.metadata['group']='rules_ai' if use_model else 'rules'
        run=AnalysisRun(run_id,snapshot.snapshot_id,now,depth,config['version']+':'+digest(config)[:12],
            'evidence-v1',model_id,'blocked' if errors else 'complete',decision,
            group='rules_ai' if use_model else 'rules')
        run.errors.extend(errors)
        run.stages.extend(['collect','rules'])
        if notifier: await notifier.emit('collect_done',20,'数据与规则检查完成')
        if not errors and use_model:
            stages=['explain'] if depth=='quick' else ['technical','fundamental','news','synthesis']
            if depth=='deep': stages += ['opposition','risk']
            ids={e.evidence_id for e in snapshot.evidence}
            for i,stage in enumerate(stages):
                try:
                    result=await asyncio.wait_for(self.model.explain(stage,snapshot,decision,prior=run.annotations),timeout=30)
                    if not isinstance(result,dict): raise ValueError('model response must be object')
                    claims=result.get('claims')
                    if not isinstance(claims,list): raise ValueError('claims must be list')
                    clean=[]
                    for claim in claims:
                        if not isinstance(claim,dict): raise ValueError('claim must be object')
                        refs=claim.get('evidence_ids')
                        if (not isinstance(refs,list) or not refs or any(not isinstance(x,str) or x not in ids for x in refs)
                            or claim.get('stance') not in ('support','opposition') or not isinstance(claim.get('text'),str)):
                            raise ValueError('invalid evidence citation')
                        clean.append(dict(text=claim['text'][:1000],evidence_ids=refs,stance=claim['stance'],
                                          stage=stage,verified=False))
                    if stage=='risk' and result.get('risk_passed') is not True:
                        raise ValueError('independent risk check did not pass')
                    run.annotations.extend(clean)
                    run.model_calls.append({'stage':stage,'model_id':result.get('model_id',model_id),
                                             'usage':result.get('usage',{})})
                except Exception as exc:
                    run.status='partial'
                    # Provider exceptions can embed request URLs/credentials; persist safe categories only.
                    run.errors.append(f'{stage}: {type(exc).__name__}')
                    if stage=='risk':
                        decision.status='observe'; decision.risk_flags.append('risk_review_failed')
                run.stages.append(stage)
                if notifier: await notifier.emit(stage,20+int((i+1)/len(stages)*70),f'{stage} 阶段结束')
        run.report=render_research_report(run,snapshot)
        await self.repository.save_run(run,snapshot)
        if run.status=='complete':
            if len(self._cache)>=128: self._cache.pop(next(iter(self._cache)))
            self._cache[key]=deepcopy(run)
        if notifier: await notifier.emit('done',100,'分析记录已保存（'+run.status+'）')
        return run
