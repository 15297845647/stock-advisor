# Execution ledger — plan: docs/superpowers/plans/2026-10-22-a-share-upgrade-plan.md

## Baseline / T01
- Source commit: b05105e; source main has a user-owned README change and the approved untracked plans. Neither changed.
- Isolated branch: codex/a-share-research-upgrade; worktree: /Users/shy/股票/stock-advisor-upgrade.
- SQLite online backup: data/backups/baseline.sqlite; PRAGMA integrity_check = ok. No credentials copied.
- Existing Python 3.13 virtual environment has aiosqlite, akshare, yaml, fastapi, dotenv and pandas. Reuse interpreter, not production database.
- No existing project test suite found outside the bundled third-party project.
- RED: tests.test_analysis_regressions.test_broken_support_is_not_a_pullback fails with 100.0 != 0, as predicted by negative support distance.

## Pre-flight / rulings
- T02 → T03/T04/T05/T07: snapshot dates must remain timezone-aware through persistence; JSON serializers must round-trip dates.
- T03 → T08: run identity is immutable; evaluation uniqueness is run + window + execution configuration, not merely stock/date.
- T05 → T07: strategy needs explicit signal close time and computed risk levels; add decision metadata for conditions/metrics and entry reference instead of parsing report text.
- Ruling: native worktree tool cannot target the original repository from this non-repository task; use sibling git worktree in the user workspace. No changes to original checkout.
- Ruling: approved plan's T01 headings differ from skill task-brief script format. Maintain this durable ledger and read task sections directly instead of rewriting approved task IDs. Preserve ledger in version control for resumability.
- Ruling: do not launch a reviewer subagent because the approved plan expressly requires user consent before any subagent; perform a separate self-review and disclose this limitation.
- Ruling: preserve the legacy path behind a version switch until user reviews deployment; the new v2 path is fully callable for isolated testing and manual preview.
- Python 3.13; dependency snapshot: akshare 1.18.64, aiosqlite 0.22.1, fastapi 0.136.3, httpx 0.25.2, pandas 3.0.3, PyYAML 6.0.3. No dependency installation or upgrade performed.

## Implementation / T02—T08
- Added explicit research adapters rather than routing v2 through legacy values with absent timestamps/units. Legacy FetchResult callers stay compatible. Current live source failures are disclosed, not converted to fresh data.
- Implemented T05's pure rule engine before T04's orchestration because orchestration consumes its decision contract; added end-to-end tests after both.
- Former marker-based decision/depth regression tests now assert the approved structured-run interface; support-score regression still directly covers the original bug.
- Independent commit checkpoints already exist for baseline, quality contracts and persistence. Remaining changes are saved in functional local commits; no merge or push.
- Snapshot integrity check now rejects altered content under an existing hash. Added safe serialization of invalid numeric values for blocked reports.
- Source calendars use official bounded annual closures because this machine's old Sina JS decoder failed. Unknown years and exhausted calendar coverage fail closed.
- Rule-only, rules+AI and historical-rule runs are distinct. The original baseline cannot be reproduced with matching frozen inputs and is explicitly unavailable; no simulated legacy score is invented.
- Historical replay validates freeze/test cutoff and evidence availability fields. Actual dataset provenance still requires human/source audit; no real historical out-of-sample result claimed.
- Unsupported corporate actions and board execution conventions are excluded. Initial simulation is a configured fee/slippage scenario, not actual brokerage execution or portfolio performance.
- Model outputs remain labelled unverified interpretations, with valid evidence IDs required. Provider exception text is not persisted because it can contain sensitive request details.
- Review fixes: post-exit price contaminating drawdown; immutable snapshot hash; pending windows fetching data unnecessarily; ranking scope/usage persistence; raw-close source attribution.

## Final verification / 2026-10-22
- Local implementation commits: `9a1eea6` (data/rules), `51059b1` (analysis/report/UI), `b497cce` (execution/evaluation). Earlier baseline/contract/persistence checkpoints are retained in branch history.
- Full suite: `/Users/shy/stock-advisor/.venv/bin/python -B -m unittest discover -s tests -q` → **54 tests passed**, 0.802 s reported by unittest. Includes authenticated API and deployment-switch compatibility.
- CLI `--help` loads all new commands successfully.
- `git diff --check` passed before documentation finalization; checked again at commit boundary.
- No live LLM calls; cost 0 CNY. Two public no-model source probes; last run `8ee7b1d9-63eb-46c8-8f3f-1c00176727cd` correctly blocked on missing quote/history. Calendar/news/financial sources returned data; history/info ConnectionError and XQ KeyError remain external-adapter limitations.
- Five synthetic HTML previews generated. Browser tool rejected local-file URL under its security policy; visual acceptance remains incomplete, no alternate surface used to circumvent.
- No futures implementation files changed; no futures live calls made. Original README, `.env`, runtime and database remain untouched.
- Detailed limitations, run instructions and rollback: [acceptance.md](acceptance.md).
- Release gate remains open for real data/model connectivity and visual review. T09 remains unstarted and cannot be completed by this development session.
