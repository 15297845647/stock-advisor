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
- Implementation is active; no T01—T09 completion claimed yet.
