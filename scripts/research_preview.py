"""Generate five offline report previews. Synthetic data; no network/model/message calls."""
import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests.fixtures.research import snapshot
from domain.models.research import EvidenceItem
from domain.report_renderer import ReportRenderer
from application.research_service import ResearchService
from repository.research_repository import ResearchRepository


async def main():
    output=ROOT/'docs/evidence/previews';output.mkdir(parents=True,exist_ok=True)
    cases=[('complete','完整示例'),('missing-news','缺少新闻'),('stale','行情过期'),
           ('empty','零候选'),('model-failure','模型失败')]
    with tempfile.TemporaryDirectory() as directory:
        repo=ResearchRepository(Path(directory)/'preview.sqlite')
        for slug,label in cases:
            s=snapshot();s.quote['name']='合成测试 · '+label
            if slug!='empty':
                for bar in s.bars[-5:]: bar['volume']=1e7
            s.fundamentals={'revenue':123456789,'available_at':None}
            s.fund_flows=[{'net_inflow':None,'status':'fixture_unavailable'}]
            s.news=[{'title':'合成新闻示例','published_at':'2026-10-22T10:00:00+08:00'}]
            s.evidence.append(EvidenceItem('news-0','news','synthetic_fixture',s.as_of.isoformat(),s.news[0],
                published_at=s.news[0]['published_at']))
            if slug=='missing-news':
                s.news=[];s.missing_fields=['news'];s.evidence=[e for e in s.evidence if e.kind!='news']
            if slug=='stale':s.quote['observed_at']='2026-10-20T15:00:00+08:00'
            model=AsyncMock();model.identity='offline_fixture'
            model.explain.return_value={'claims':[{'text':'合成测试解释，不代表真实市场判断',
                'stance':'support','evidence_ids':['bars']}],'risk_passed':True}
            if slug=='model-failure':model.explain.side_effect=TimeoutError('synthetic timeout')
            source=AsyncMock();source.collect.return_value=s
            run=await ResearchService(source,repo,model).analyze_run('600000','quick',as_of=s.as_of)
            report='# 合成测试数据，仅检查展示与状态\n'+run.report
            html=ReportRenderer().render_html('600000',label,'快速',report)
            (output/(slug+'.html')).write_text(html)
        links=''.join(f'<li><a href="{slug}.html">{label}</a></li>' for slug,label in cases)
        (output/'index.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"><title>研究报告预览</title>'
            '<style>body{font:18px system-ui;max-width:760px;margin:50px auto;padding:24px}li{margin:20px}</style>'
            '<h1>A股分析 · 五类报告预览</h1><p>全部使用合成数据；没有调用模型、推送消息或生成真实绩效。</p>'
            '<ul>'+links+'</ul></html>')
    print(output/'index.html')


if __name__=='__main__':asyncio.run(main())
