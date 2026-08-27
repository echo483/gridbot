---
name: verify-and-land
description: Use before committing ANY gridbot change, especially executor/capture code — the verification gauntlet that has caught every silent failure so far (masked pytest, vacuous replays, stray edits). Landing without it has shipped broken code twice.
---

# Verify and land (gridbot)

Every step below exists because skipping it shipped a real defect.

## The gauntlet, in order

1. **Bare pytest, never piped.** `uv run pytest -q` with NO `2>&1 | tail`
   and NO `&&` chain to commit. Piped runs masked failing suites TWICE
   (08-27): read the summary line yourself, then act. A `;` before
   `git commit` also landed a broken file once — only `&&` after a bare
   verified run.
2. **Replay the real days.** `uv run python scripts/replay_harness.py
   data/shadow/mapentry-YYYYMMDD.jsonl` for each armed day (bare
   YYYYMMDD — dashed dates don't exist; scripts/_jsonl.py owns this).
   The harness must report REAL placements (e.g. 53/33 on 08-27); it
   once vacuously "passed" on 0 placements from a wrong input file.
   It now exits 1 on empty input — treat that as a wrong-file signal.
3. **`git status` before batch-adds.** A stray wording edit in
   kelly.py (not authored this session) nearly rode a `git add src`
   into a commit (08-27). Inspect any file you don't remember touching;
   revert what you didn't intend.
4. **Lint the touched files only**: `uv run ruff check <files>` — fix
   mechanical findings; the repo's deliberate idioms (BLE001 fail-soft
   catches, dispatch PLW1510) stay.
5. **One coherent commit** with the evidence in the message (test count,
   replay numbers), then push. Trading-path changes cite their review
   provenance (which findings, which reviewer).
6. **Telegram the user** via `hermes send -t telegram "..."` for
   milestone batches — consolidated, not per-commit.

## Hard rails that override everything

- dry_run flips live ONLY by the user's own hand. Never in code, tests,
  or dispatch prompts.
- Policy changes pre-register in docs/experiments/registry.jsonl BEFORE
  the instrument runs under them.
- Capture continuity outranks the refactor: never restart capture for a
  code change; the executor (dry-run) restarts freely and is
  restart-safe by design — verify with the ledger-recovery tests.
