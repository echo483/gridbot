# Incident: locked keyring took GRID capture dark — 2026-08-31

**Rail 4 breach.** Second occurrence in six days of a desktop-session
lifecycle event costing capture (08-30: machine powered off, 6h31m; today:
gnome-keyring restarted locked). Neither was a code fault.

## Timeline (PDT)

| time | event |
|---|---|
| 10:14:21 | gnome-keyring-daemon restarts (pids 189652/189653, `--components=secrets`). `config._secret` logs `keyring degraded (InitError); serving last good value from in-process cache`. **Capture still healthy** — the 600s cache covers it. |
| 10:24:15 | last successful GRID series-state poll (HTTP 200). |
| 10:24:17 | cache exceeds `KEYRING_CACHE_MAX_AGE_S=600` → `treating as UNAVAILABLE (fail closed)`. **GRID capture stops.** |
| 10:25:33 | `grid_ss` counter freezes at 29085 / `polls` at 29003. |
| 10:49 | `health_check` crosses `GRID_FRESH_S=25min`, writes `ok:false, problems:["GRID data stale"]`, sends its Telegram transition alert. Working as designed. |
| 11:07:33 | still blocked. 42 min dark, `tracked=25 live=7`. |

## Diagnosis: LOCKED, not deleted

`org.freedesktop.secrets` is registered on the user bus (pid 189653), so the
daemon is alive and reads fail `InitError` rather than returning absence. The
same three secrets read PRESENT at 2026-08-30 12:47 in the ops session, so the
items exist — access is blocked. This is the documented 08-26 mode: "came back
LOCKED (reads BLOCK forever)".

## Measured loss

Pre-outage GRID snapshot rate, from the 5-minute heartbeat deltas
(09:30→10:25): 224, 213, 192, 188, 192, 187, 197, 253, 250, 230 per 5 min —
**~42 series-state snapshots/min**.

At 42 minutes dark that is **≈1,760 GRID series-state snapshots lost**, across
**7 live CS2 series**. Permanently uncapturable; GRID Open Access is polling
only, with no backfill.

**The join is what actually died.** Polymarket capture was unaffected and kept
running the whole time — `pm_books` 976,948 (10:25) → 1,023,890 (11:07),
**+46,942 book snapshots**. So for this window the corpus holds market prices
with no game state to join them to. Orphaned book data is not merely a smaller
corpus, it is a window that can never produce a mapentry row, a q_round, or a
verdict instance. Prices without signal have no experimental use.

## What worked

- **Fail-closed was correct.** The bounded cache (600s) is what stops a wedged
  keyring from running an armed engine on a possibly-deleted credential
  (08-26 peer review, finding 1). It behaved exactly as specified.
- **`health_check` detected it** at its 25-minute threshold and alerted. Not a
  monitoring gap — just a slower threshold than the ops-session watchdog (10m).
- **PM capture is independent** of the GRID key and never dropped.
- **No unit was restarted.** Rail 4. `config.py:160` discards the degraded flag
  on the first healthy read and logs `keyring healthy again`, and capture reads
  the key per poll, so recovery is automatic on unlock.

## What this exposed (fixed today, commit 04cd515)

`scripts/health_check.py:_escalate()` read its webhook secret through a **raw
`keyring.get_password()` with no timeout** — the call `tests/conftest.py`
documents as able to "block forever behind a GUI prompt". `_escalate` runs only
when a unit is DOWN and self-heal has already failed, i.e. when the keyring is
a likely culprit, and **a hang is not an exception**, so its `except Exception`
would not have caught it. The 5-minute watchdog would have wedged permanently
at the moment it was most needed. Now reads through `gridbot.config._secret`
(5s reaper thread, bounded cache, fail-closed). First test coverage that file
has ever had: 3 tests, 2 written to fail first.

We did not hit it today only because `_escalate` fires on unit-down, not on
data staleness, and all five units stayed active throughout.

## Recovery (user only — needs the login password)

```
gnome-keyring-daemon --replace --unlock
```

Expect **no dialog**: the gcr prompter is commonly dead here, so it reads the
password from stdin in that terminal. No unit restart afterward.

The `GRID_API_KEY` env-override path exists and is **not recommended** — it
puts key material into a process environment, against rail 6.

## Open question for the user

Two capture losses in six days, both from workstation session lifecycle, zero
from code. The corpus is the moat and it is being cut by the host, not the
software. The always-on-host question (and its conflict with rail 6 — capture
reads the GRID key per poll, so an always-on host wants an unlocked-at-boot
keyring) is now the highest-value ops decision open. Raised 08-30, unresolved.

— orchestrator lane, 2026-08-31 11:10 PDT (outage ongoing at write time)
