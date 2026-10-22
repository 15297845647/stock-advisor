import unittest
from unittest.mock import AsyncMock,patch
from dataclasses import asdict
from tests.fixtures.research import snapshot
from tests.test_research_repository import run_fixture


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_requires_auth_and_returns_frozen_record(self):
        import httpx
        from fastapi import FastAPI
        from admin.api import router
        from admin.auth import verify_token
        app=FastAPI();app.include_router(router)
        repo=AsyncMock();s=snapshot();r=run_fixture(s)
        repo.get_run.return_value=r;repo.get_snapshot.return_value=s
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            self.assertEqual((await client.get('/api/research/runs')).status_code,401)
            app.dependency_overrides[verify_token]=lambda:None
            with patch('repository.research_repository.ResearchRepository',return_value=repo):
                response=await client.get('/api/research/runs/r1')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['run']['decision']['run_id'],'r1')
            self.assertEqual(response.json()['snapshot']['content_hash'],s.content_hash)

    async def test_rollback_switch_preserves_legacy_entry(self):
        from application.analysis_service import AnalysisService
        svc=AnalysisService.__new__(AnalysisService)
        svc.report_repo=AsyncMock();svc.report_repo.get_today_report.return_value=type('Report',(),{'content':'original'})()
        svc.analyze_run=AsyncMock(return_value=type('Run',(),{'report':'v2'})())
        with patch.dict('os.environ',{'STOCK_ANALYSIS_ENGINE':'legacy'}):
            self.assertEqual(await svc.analyze_stock('600000'),'original')
        with patch.dict('os.environ',{'STOCK_ANALYSIS_ENGINE':'v2'}):
            self.assertEqual(await svc.analyze_stock('600000'),'v2')
        self.assertEqual(svc.report_repo.get_today_report.await_count,1)
