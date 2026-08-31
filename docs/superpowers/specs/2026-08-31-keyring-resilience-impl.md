# Keyring resilience — implementation notes (A+C, ops lane)

Spec: `docs/superpowers/specs/2026-08-31-keyring-resilience.md` (user sign-off
2026-08-31: A+C approved, D = always-on host still an open decision).
This file records what was built and the acceptance-test status. Ops lane only:
scripts/ + systemd user units, no src/ changes, no secrets anywhere.

## B-verify (one-time check, done 2026-08-31)

- `/etc/pam.d/sddm` carries BOTH required lines:
  `auth optional pam_gnome_keyring.so` (line 10) and
  `session optional pam_gnome_keyring.so auto_start` (line 28).
- Conclusion: fresh graphical login / reboot lands with the login collection
  UNLOCKED. The 08-26 and 08-31 incidents were both MID-SESSION daemon
  restarts, which PAM does not cover — that is what the A+C watchdog is for.
- Runbook line: after a reboot, no unlock step is needed; after a mid-session
  keyring daemon restart, run `gnome-keyring-daemon --replace --unlock`
  (password on stdin; capture self-heals, no unit restart).

## A+C watchdog (built 2026-08-31)

- `scripts/keyring_watch.py` — standalone probe + Telegram alerter, callable
  from the health timer path (no timer/unit edits required to ship):
  - Probe: Secret Service `org.freedesktop.Secret.Collection` `Locked`
    property over D-Bus. Boolean only; zero secret material. Bounded ≤5 s.
  - Fail loud: D-Bus timeout/error ⇒ treated as locked=True (announcement,
    not silence). Exit code 0=unlocked/ok, 2=locked/alerted, 3=probe error.
  - Alerting: first lock → immediate Telegram alert with the recovery
    one-liner (no secret bytes); re-alert at ≥15-min cadence while locked;
    exactly one `recovered` transition alert on unlock. State key:
    `/home/ops/gridbot/data/ops/keyring_watch_state.json` (gitignored dir).
  - Cadence guard: skips alerting if health_check already sent a keyring alert
    in the current cycle (dedup, no double-ping).
- Wired into `scripts/health_check.py` (runs after the unit self-heal block,
  before the summary alert; its transition alerts ride the existing Telegram
  path; a keyring alert suppresses the generic red summary in the same cycle
  to keep the phone quiet).
- Acceptance test (spec §4): unit tests hermetic + mocked D-Bus live in
  `tests/test_keyring_watch.py` (locked/unlocked/timeout→fail-loud, zero
  secret-shaped strings in alert output). LIVE DRILL (user-supervised, lock a
  TEST collection — never the login collection mid-match) is the remaining
  PASS condition and needs Nick at the box; detection-latency bar ≤5 min,
  15-min re-alert cadence, one recovered transition, capture self-heals.
- Replay bar: with A+C live, the 08-31 lock (10:14) alerts ≤10:19 instead of
  10:49. Any future locked-keyring outage alerting later than one health
  cycle after the lock event = FAIL of this lane.

## Post-review adjustments (2026-08-31)

- Review A (logic, glm-5.3) MAJOR fixed: health_check subprocess budget for
  keyring_watch raised 20s → 45s (worst case 5s probe + 30s Telegram send +
  margin; a killed send previously lost the state file write → re-alert every
  cycle). 674 tests green after fix.
- Known MINOR (accepted, documented): state timestamps use CLOCK_MONOTONIC,
  which resets at reboot — a locked state file surviving a reboot starves
  re-alerts up to 900s post-boot. Usually moot (login auto-unlocks via PAM,
  B-verified). Review A verdict on the fix round: all other items OK.
- Open: the mocked-probe unit tests do not exercise the real D-Bus reply
  parse (_probe_locked_once) — §4's unit criteria complete only with the
  user-supervised live drill (TEST collection, never login mid-match).
