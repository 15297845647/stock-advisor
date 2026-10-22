"""Screen an explicit universe once; compare rule and optional model ranking."""
import asyncio
import json
from copy import deepcopy
from datetime import datetime
from uuid import uuid4
from domain.data_quality import SH
from domain.models.research import json_text
from application.snapshot_service import SnapshotService
from application.research_service import ResearchService
from repository.research_repository import ResearchRepository


class FrozenSnapshotSource:
    def __init__(self,snapshot): self.snapshot=snapshot
    async def collect(self,code,as_of):
        if code!=self.snapshot.stock_code: raise ValueError('snapshot symbol mismatch')
        return deepcopy(self.snapshot)


async def rank_candidates(runs):
    from infrastructure.llm import get_llm_router
    from domain.models.llm_task import LLMRequest, LLMTaskType
    req=LLMRequest(task_type=LLMTaskType.RECOMMEND_JUDGE,
        system_prompt='只对输入候选排序，不添加股票，不预测价格。输出JSON数组，包含每个输入代码一次，不输出其他文本。',
        messages=[{'role':'user','content':json_text([{'code':r.decision.stock_code,
            'metrics':r.decision.metadata,'risk':r.decision.risk_flags} for r in runs])}],max_tokens=1000)
    result=await get_llm_router().chat(req)
    return {'codes':json.loads(result.content),'model_id':result.provider+'/'+result.model,
            'usage':{'tokens':result.total_tokens,'cost_cny':result.cost_cny,'latency_ms':result.latency_ms}}


class ResearchSelectionService:
    def __init__(self, snapshots=None, repository=None, model=None, ranker=None):
        self.snapshots=snapshots or SnapshotService()
        self.repository=repository or ResearchRepository()
        self.model=model
        self.ranker=ranker or rank_candidates

    async def screen(self,codes,*,as_of=None,strategy_id='pullback',use_model=True,scope='explicit_universe_not_full_market'):
        now=as_of or datetime.now(SH)
        codes=sorted(set(codes))
        if len(codes)>30: raise ValueError('maximum 30 explicitly selected symbols per batch')
        batch={'batch_id':str(uuid4()),'created_at':now.isoformat(),'universe':codes,'strategy':strategy_id,
            'rule_ranking':[],'ai_ranking':[],'runs':{'rules':{},'rules_ai':{}},'errors':[],
            'legacy':{'status':'unavailable','reason':'no_original_baseline_with_matching_frozen_inputs'},
            'scope':scope,'model_enabled':use_model,'ranking_model_call':None}
        frozen={};runs=[]
        for code in codes:
            try:
                s=await self.snapshots.collect(code,now)
                frozen[code]=s
                svc=ResearchService(FrozenSnapshotSource(s),self.repository,self.model)
                run=await svc.analyze_run(code,'quick',as_of=now,strategy_id=strategy_id,use_model=False)
                batch['runs']['rules'][code]=run.run_id
                if run.decision.status=='eligible': runs.append(run)
            except Exception as exc:
                batch['errors'].append(code+': '+type(exc).__name__)
        runs.sort(key=lambda r:(-r.decision.rule_score,r.decision.stock_code))
        runs=runs[:10]
        batch['rule_ranking']=[r.decision.stock_code for r in runs]
        if use_model and runs:
            try:
                ranked=await asyncio.wait_for(self.ranker(runs),30)
                if isinstance(ranked,dict):
                    batch['ranking_model_call']={k:ranked.get(k) for k in ('model_id','usage')}
                    ranked=ranked.get('codes')
                if (not isinstance(ranked,list) or any(not isinstance(x,str) for x in ranked) or
                    len(ranked)!=len(runs) or set(ranked)!=set(batch['rule_ranking'])):
                    raise ValueError('ranking_invalid')
                for code in ranked:
                    svc=ResearchService(FrozenSnapshotSource(frozen[code]),self.repository,self.model)
                    run=await svc.analyze_run(code,'quick',as_of=now,strategy_id=strategy_id)
                    batch['runs']['rules_ai'][code]=run.run_id
                    if run.status=='complete' and run.decision.status=='eligible': batch['ai_ranking'].append(code)
            except Exception as exc:
                batch['errors'].append('ranking_invalid' if isinstance(exc,ValueError) else 'ranking_failed')
        await self.repository.save_batch(batch)
        return batch


def format_batch(batch):
    chosen=batch['ai_ranking'] if batch['model_enabled'] else batch['rule_ranking']
    lines=[f'研究批次：{batch["batch_id"]}',f'策略：{batch["strategy"]}；检查股票数：{len(batch["universe"])}',
           '规则候选：'+('、'.join(batch['rule_ranking']) or '无')]
    if batch['model_enabled']: lines.append('AI核查后候选：'+('、'.join(chosen) or '无；未通过或核查未完成'))
    if not chosen: lines.append('本次没有可输出的合格候选，不进行无依据补选。')
    if batch['errors']: lines.append('限制：'+'；'.join(batch['errors']))
    lines.append('规则参数尚待验证；完整依据可在后台“研究记录与验证”查看。')
    return '\n'.join(lines)
