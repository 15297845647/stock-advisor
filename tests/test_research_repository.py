import importlib.util
import tempfile
import unittest
from pathlib import Path
from dataclasses import asdict
from tests.fixtures.research import snapshot
from domain.models.research import AnalysisRun, ResearchDecision


def run_fixture(s=None, run_id='r1'):
    s = s or snapshot()
    return AnalysisRun(run_id, s.snapshot_id, s.as_of, 'quick', 'swing-v1', 'evidence-v1',
                       'none', 'complete', ResearchDecision('d1', run_id, s.stock_code))


class RepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(importlib.util.find_spec('repository.research_repository'))
        from repository.research_repository import ResearchRepository
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = ResearchRepository(str(Path(self.temp.name)/'test.sqlite'))

    async def test_idempotent_roundtrip_without_text_markers(self):
        s = snapshot()
        r = run_fixture(s)
        r.report = 'plain report'
        await self.repo.save_run(r,s)
        await self.repo.save_run(r,s)
        result = await self.repo.get_run('r1')
        self.assertEqual(asdict(result), asdict(r))
        self.assertEqual(len(await self.repo.list_runs()),1)
        self.assertEqual(asdict(await self.repo.get_snapshot(s.snapshot_id)),asdict(s))

    async def test_immutable_and_atomic(self):
        s = snapshot()
        r = run_fixture(s)
        await self.repo.save_run(r,s)
        r.report = 'changed'
        with self.assertRaises(ValueError):
            await self.repo.save_run(r,s)
        self.assertEqual((await self.repo.get_run('r1')).report,'')
        r = run_fixture(s,'r2')
        r.decision.run_id = 'other'
        with self.assertRaises(ValueError):
            await self.repo.save_run(r,s)
        self.assertIsNone(await self.repo.get_run('r2'))

    async def test_evaluation_key_has_config(self):
        from domain.models.research import EvaluationResult
        s = snapshot()
        await self.repo.save_run(run_fixture(s),s)
        e = EvaluationResult('r1',5,'pending')
        await self.repo.save_evaluation(e,'a')
        await self.repo.save_evaluation(e,'a')
        await self.repo.save_evaluation(e,'b')
        self.assertEqual(len(await self.repo.list_evaluations()),2)

    async def test_migration_preserves_old_rows(self):
        import sqlite3
        db=sqlite3.connect(self.repo.path)
        db.execute('CREATE TABLE analysis_reports (report_content TEXT)')
        db.execute("INSERT INTO analysis_reports VALUES ('old')")
        db.commit(); db.close()
        await self.repo.initialize()
        await self.repo.initialize()
        db=sqlite3.connect(self.repo.path)
        self.assertEqual(db.execute('SELECT report_content FROM analysis_reports').fetchone()[0],'old')
        db.close()
