# Keyring watchdog live drill — 2026-08-31

Spec: `docs/superpowers/specs/2026-08-31-keyring-resilience.md` §4 (remaining
PASS condition). Status: **ABORTED AT P1 — gcr prompter dead; detection path
P0 verified, alert-path clauses PENDING re-run.** Log:
`data/ops/keyring_drill-20260831.jsonl`.

## Pre-drill fixes (shipped this session, uncommitted — ops session lands)

- GAP-1: `--collection-label LABEL` on `--check`/`--notify`
  (`scripts/keyring_watch.py`: `_probe_locked_once(collection_label)` resolves
  via `secretstorage.get_all_collections` by label; unknown label ⇒ ProbeError,
  fail loud/locked path). Default (no flag) unchanged.
- GAP-2: `--state-file PATH` on `--notify`/`--reset`
  (`_load_state`/`_save_state` take the path; drill uses
  `data/ops/keyring_watch_drill_state.json`, no crosstalk with the production
  state file). Default unchanged.
- Tests: 5 new hermetic tests in `tests/test_keyring_watch.py` following the
  existing mocked-D-Bus pattern; conftest isolation intact.
- Suite: `692 passed in 4.52s` (baseline 687, count grew, all green).

## Spec §4 PASS clauses vs observed

| # | Clause | Observed | Result |
|---|--------|----------|--------|
| 1 | Real D-Bus reply parse exercised (`--check` on the default collection, the open item in the impl notes) | P0: `keyring_watch.py --check` exit 0, real jeepney reply parsed (logged 2026-09-01T06:10:53Z) | PASS |
| 2 | Locked TEST collection detected by probe (`--check --collection-label gridbot-drill` exit 2) | Not reached — P1 collection creation aborted (see finding) | PENDING (log path above) |
| 3 | First locked alert immediate; re-alert at ≥15-min cadence; no spam within cadence | Not reached (depends on P1) | PENDING (log path above) |
| 4 | Exactly one recovered transition alert on unlock (state-file verified) | Not reached (depends on P1) | PENDING (log path above) |
| 5 | No impact on capture by design (TEST collection only; real-path replay already done live 08-31) | `systemctl --user is-active gridbot-capture` = active at drill start, after P1 abort, and at final log write; drill never touched the login collection | PASS (by design) |

## Acceptance finding: gcr prompter dead (P1 abort)

`secretstorage.create_collection` timed out twice (90s, then 300s) with no
passphrase dialog ever appearing for the user. Diagnosis at 2026-09-01T06:21Z:

- `org.gnome.keyring.PrivatePrompter` and `org.gnome.keyring.SystemPrompter`
  both listed as `(activatable)` — **no running prompter process**.
- `ps aux | grep prompter` → nothing (only gcr-ssh-agent runs).
- No drill collection was created (verified via `get_all_collections`:
  only `session` and `Login` exist). Nothing stored in any collection; the
  login collection was never touched.
- `gnome-keyring-daemon --start` is running (pid 189653, started 10:14 — the
  08-31 restart) but its prompter is not; this is the same dead-prompter
  posture the 08-26 incident left the box in.

This is precisely the watchdog's threat model: the keyring is up but the
prompter path is dead, so any future mid-session daemon restart would present
the same dead end. Per the drill plan, the P1 abort is itself a valid
acceptance finding, not an operator error.

## Consequences / recommendations

1. Re-run the drill after a fresh graphical login (PAM auto-starts the
   prompter with the session); P0-P4 take ~10 min, P5-P8 add the 930s
   cadence wait. The driver (`scripts/_tmp_keyring_drill.py --resume-from`)
   supports picking up any phase.
2. The dead prompter tonight means the documented recovery
   (`gnome-keyring-daemon --replace --unlock`, passphrase on stdin) is the
   ONLY viable unlock path right now — worth confirming on the next drill
   that the P6 prompt does appear post-login.
3. P2+ logging hooks and capture-liveness checks are already in the driver;
   no rework needed for the re-run.

## Capture impact

None. `gridbot-capture` active throughout (checked at drill start, at P1
abort, and at log close). Drill holds zero secret material — it creates an
empty throwaway collection and probes boolean `Locked` state only.
