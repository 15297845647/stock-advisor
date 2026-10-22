"""Append-only research runs; evaluation upserts are keyed by execution config."""
from dataclasses import asdict
from pathlib import Path
import json
import aiosqlite
from domain.models.research import AnalysisRun, AnalysisSnapshot, json_text

SCHEMA = '''
CREATE TABLE IF NOT EXISTS research_snapshots (
 snapshot_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_runs (
 run_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES research_snapshots(snapshot_id),
 created_at TEXT NOT NULL, stock_code TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_decisions (
 decision_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES research_runs(run_id),
 payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_evaluations (
 run_id TEXT NOT NULL REFERENCES research_runs(run_id), horizon_days INTEGER NOT NULL,
 config_hash TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(run_id,horizon_days,config_hash));
CREATE TABLE IF NOT EXISTS research_batches (
 batch_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL);
'''


class ResearchRepository:
    def __init__(self, path=None):
        if path is None:
            from agent.config import DB_PATH
            path = DB_PATH
        self.path = str(path)

    async def initialize(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def save_run(self, run: AnalysisRun, snapshot: AnalysisSnapshot):
        if (run.snapshot_id != snapshot.snapshot_id or run.decision.run_id != run.run_id or
                run.decision.stock_code != snapshot.stock_code):
            raise ValueError('run/snapshot/decision identity mismatch')
        snapshot_data = asdict(snapshot)
        # First collection time belongs to the run; equal content is shared across runs.
        snapshot_payload = json_text(snapshot_data)
        run_payload = json_text(asdict(run))
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            await db.execute('PRAGMA foreign_keys=ON')
            await db.execute('BEGIN IMMEDIATE')
            async with db.execute('SELECT payload FROM research_runs WHERE run_id=?',(run.run_id,)) as cur:
                existing = await cur.fetchone()
            if existing:
                if existing[0] != run_payload:
                    raise ValueError('immutable run already exists')
                return
            await db.execute('INSERT OR IGNORE INTO research_snapshots VALUES (?,?)',
                             (snapshot.snapshot_id,snapshot_payload))
            await db.execute('INSERT INTO research_runs VALUES (?,?,?,?,?)',
                (run.run_id,run.snapshot_id,run.created_at.isoformat(),snapshot.stock_code,run_payload))
            await db.execute('INSERT INTO research_decisions VALUES (?,?,?)',
                             (run.decision.decision_id,run.run_id,json_text(asdict(run.decision))))
            await db.commit()

    async def _payloads(self, query, args=()):
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(query,args) as cur:
                return [json.loads(r[0]) for r in await cur.fetchall()]

    async def get_run(self, run_id):
        rows = await self._payloads('SELECT payload FROM research_runs WHERE run_id=?',(run_id,))
        return AnalysisRun.from_dict(rows[0]) if rows else None

    async def get_snapshot(self, snapshot_id):
        rows = await self._payloads('SELECT payload FROM research_snapshots WHERE snapshot_id=?',(snapshot_id,))
        return AnalysisSnapshot.from_dict(rows[0]) if rows else None

    async def list_runs(self, limit=200):
        rows = await self._payloads('SELECT payload FROM research_runs ORDER BY created_at DESC, run_id LIMIT ?',
                                   (min(max(limit,1),10000),))
        return [AnalysisRun.from_dict(r) for r in rows]

    async def save_evaluation(self, result, config_hash):
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            await db.execute('PRAGMA foreign_keys=ON')
            await db.execute('INSERT INTO research_evaluations VALUES (?,?,?,?) '
                'ON CONFLICT(run_id,horizon_days,config_hash) DO UPDATE SET payload=excluded.payload',
                (result.run_id,result.horizon_days,config_hash,json_text(asdict(result))))
            await db.commit()

    async def list_evaluations(self):
        return await self._payloads('SELECT payload FROM research_evaluations ORDER BY run_id,horizon_days,config_hash')

    async def save_batch(self, batch):
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            await db.execute('INSERT INTO research_batches VALUES (?,?,?)',
                             (batch['batch_id'],batch['created_at'],json_text(batch)))
            await db.commit()
