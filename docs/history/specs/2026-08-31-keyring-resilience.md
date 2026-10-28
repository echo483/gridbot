# Keyring resilience — options for the locked-keyring capture failure mode

Status: PROPOSAL ONLY (no src/ changes, no registry changes, no code in the
trade path). Written 2026-08-31 by gridbot-reasoner after the second locked-
keyring outage. Implementation lives in scripts/ + systemd user units + docs
(ops lane) only. Facts below are from `docs/STATE.md` gotchas and
`docs/analysis/2026-08-31-keyring-capture-outage.md` — not re-derived.

## 0. Problem (verified record)

- gnome-keyring is a single point of failure that fails BOTH ways (08-26):
  crash killed the armed engine; restart came back LOCKED and reads block
  forever behind a dead gcr prompter.
- **08-31 recurrence**: keyring restarted locked 10:14; `config._secret`
  served its 600s last-good cache until 10:24, then failed closed; GRID
  capture dark 10:24→12:08 (104 min, ≈4,370 series-state snapshots, 7 live
  series). PM capture ran throughout (+107,452 book snapshots) — the window
  holds prices with no game state: permanently unusable for mapentry/q_round/
  verdict rows. GRID OA is polling-only, no backfill.
- 08-30: host powered off 6h31m. Three capture losses in six days, **zero
  from code — the host session lifecycle is the fault class** (rail 4 moat).
- Recovery is manual and password-bound: `gnome-keyring-daemon --replace
  --unlock` (reads password from stdin when the prompter is dead). Capture
  self-heals on unlock with NO unit restart (`config.py:160` drops the
  degraded flag on first healthy read).
- Existing mitigations: `_secret` 5s-timeout read thread + 600s cache +
  fail-closed (04cd515 also fixed `health_check._escalate` to use it);
  health timer 5m with GRID_FRESH_S=25min threshold → Telegram transition
  alert; units anchored to graphical-session.target; capture under
  systemd-inhibit. The 08-31 alert fired 10:49 — **35 min after the lock
  event**, because detection keys off data staleness, not keyring state.
- The `GRID_API_KEY` env-override path exists and stays NOT recommended
  (key material in process env = rail-6 breach).

## 1. Constraints (iron rails as applied here)

1. No secret value may appear in any file, env, log, alert text, or prompt.
   Detection must work on **boolean lock state**, never by printing secrets.
2. No automated recovery may require storing the login password anywhere.
   Any option whose only recovery path is a stored passphrase is rejected.
3. Fail-closed semantics of `_secret` stay — a bounded cache exists to stop
   an armed engine running on a possibly-deleted credential; do not extend
   the 600s window to "ride through" locks.
4. All new code in scripts/ + systemd units; zero src/ or trade-path edits.

## 2. Options matrix

### A. Early locked-state detection + tightened Telegram alert cadence
Probe keyring lock state directly instead of waiting for data staleness.
Mechanism: Secret Service collections expose a boolean `Locked` D-Bus
property (`org.freedesktop.Secret.Collection`); reading it needs no password
and returns no secret material. A scripts/-lane probe (bounded ≤5s, same
reaper pattern as `_secret`) runs from the existing 5m health timer; on
locked → Telegram immediately with the recovery one-liner (no secret text),
re-alert every 15 min while locked, one "recovered" transition alert.
- Security tradeoff vs rail 6: **none**. No key material moves; probe output
  is a boolean. Alert text is redaction-checked.
- Failure modes: D-Bus itself wedged (08-26 mode) → probe times out, treated
  as LOCKED (fail loud); alert fatigue if user is away for hours (bounded by
  15-min cadence + transition dedup, same pattern health_check already uses);
  does NOT shorten outage when the user is unreachable — it converts a silent
  104-min outage into a known 104-min outage and cuts MTTR to ≤5 min + human
  response time when the user IS reachable.
- 08-31 replay: alert at ≤10:19 instead of 10:49.

### B. PAM-based auto-unlock on user login
pam_gnome_keyring already auto-unlocks the "login" collection at graphical
login (password flows through PAM into the daemon; no file copy — rail-6
compliant as-is). Both incidents were MID-SESSION daemon restarts, not
logins, so PAM covers the login path only.
- Action: verify the PAM wiring is intact (zero-cost check, one-time) so a
  fresh login/reboot always lands unlocked; document it in the runbook.
- Security tradeoff: none at verification; extending to AUTOLOGIN+unlock
  (to make reboots self-healing) trades physical-access security — anyone at
  the box gets a desktop and an unlocked keyring. Acceptable only on a
  dedicated, physically secured host; not on the daily driver.
- Failure modes: PAM stack order silently broken → locked-at-login with no
  error (detect via option A probe); autologin variant leaves funds keys
  (PMUS) unlocked unattended — see option D for the containment.

### C. systemd session-lifecycle hooks
A user-level watchdog unit (PartOf=graphical-session.target) plus lifecycle
hooks: watch gnome-keyring-daemon pid churn and session start/stop; on daemon
restart or session end, run the A-probe immediately (don't wait for the next
timer tick) and alert. On session stop, alert "capture stopping: session
ended" so the 08-30 power-off class is also announced, not discovered.
- Security tradeoff vs rail 6: **none** as specified — detection/alerting
  only. There is NO passwordless re-unlock: `--replace` without `--unlock`
  comes back locked, and any hook that stored the passphrase to auto-unlock
  is a rail-6 breach and is excluded by construction.
- Failure modes: watchdog dies with the user manager on logout (then the
  session-stop alert is the last word — acceptable, capture is PartOf the
  session anyway); pid-watch can miss an in-place daemon respawn (the lock
  probe remains the ground truth, pid churn only triggers early probes);
  hooks add moving parts for a rare event — keep it ≤1 small script + 1 unit.

### D. Always-on host (decision framing; the top open ops question)
The losses are host-lifecycle, not code; option A+C cuts MTTR but cannot
exceed human response time. A 24/7 host removes the class. The rail-6
conflict is real: an always-on host wants a keyring unlocked at boot.
- Compliant framing — **split by secret criticality**: GRID_API_KEY is a
  free, rate-limited, read-only public-data credential with no funds behind
  it; PMUS keys control money. Put GRID capture on the always-on host with
  autologin + pam_gnome_keyring (password via PAM at login, never in a file);
  keep PMUS keys ONLY on the interactive workstation, whose keyring stays
  manually unlocked. The auto-unlocked host then holds only low-value
  material; an armed session is manual by design (queue item 6) and never
  needs the always-on host.
- Security tradeoff: physical access to the always-on box yields the GRID key
  (low value: worst case someone polls public data on our quota) — explicitly
  NOT the funds keys. This is the rail-6-compatible version of the trade.
- Failure modes: new-host setup/migration (setup_secrets.py there; keyring
  must be the GNOME "login" collection, not KWallet); two hosts to monitor
  (health checks extend to it); the host itself can still power-off (UPS /
  BIOS power-restore config is a cheap sub-fix); autologin session must not
  suspend (systemd-inhibit pattern carries over).

## 3. Ranked recommendation

1. **A + C together** (one watchdog: lock probe + pid/session hooks + alert
   cadence). Zero rail-6 cost, scripts/-only, attacks the demonstrated gap
   (35-min detection latency) directly. Do first.
2. **B-verify**: one-time PAM wiring check + runbook line. Free insurance for
   the reboot path; B-autologin ONLY as part of option D, never on the
   workstation holding PMUS keys.
3. **D as the user's decision**, framed above: if capture continuity keeps
   costing corpus, the always-on host with secret-criticality split is the
   rail-6-compatible end state. A+C is not a substitute — it bounds MTTR at
   human response time; D removes the failure class.
4. Rejected by construction: stored-passphrase auto-unlock hooks; extending
   the `_secret` cache TTL to ride through locks; env-override as a recovery
   path (all rail-6 or fail-closed breaches).

## 4. Pre-registered acceptance test (for A+C; frozen before implementation)

- Unit (hermetic, no live keyring): probe returns locked=True ≤5s against a
  mocked locked collection, locked=False against unlocked; on D-Bus
  timeout/error → locked=True (fail loud). Assert zero secret-shaped strings
  in any log/alert output (redaction registry check).
- Live drill (user-supervised, any hour; capture may go dark briefly by
  design): lock a TEST collection via D-Bus Lock() (never the login
  collection mid-match). PASS iff: (1) detection latency ≤5 min from lock;
  (2) first Telegram alert carries the recovery command and no secret bytes;
  (3) re-alerts at 15-min cadence while locked, no spam between; (4) unlock →
  exactly one "recovered" transition alert; (5) capture self-heals with no
  unit restart (replay of the 08-31 recovery path).
- Replay bar: had A+C been live on 08-31, first alert would have fired
  ≤10:19 (vs actual 10:49). Any future locked-keyring outage alerting later
  than one timer cycle after the lock event = FAIL of this lane.

## 5. Open questions for the user (decisions, not code)

1. Approve A+C build in the ops lane (scripts/ + user units)?
2. Always-on host: buy/dedicate one? If yes, confirm secret-criticality
   split (GRID-only auto-unlocked host; PMUS keys stay on the workstation).
3. Drill window for the acceptance test.
— gridbot-reasoner, 2026-08-31. Uncommitted by design; parent review first.
