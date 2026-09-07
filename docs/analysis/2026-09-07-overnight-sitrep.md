# SITREP — overnight 2026-09-06/07

For Nick, on waking. Branch `capture/latency-and-evidence-flow`, **4 commits, not
pushed** (your call). Tests **1005 green**, `ruff check .` green, all six units
active, `dry_run` untouched, no orders, no live exposure.

---

## The one-paragraph version

The capture layer was structurally blind and nobody knew. `pm_discovery` swept an
unfiltered event list against a pagination ceiling it could never clear, so **not
one CS2 match market had ever been captured on the intl venue** — every CS2 row in
the entire tape history was a single season-long future. That is fixed, and the
intl CS2 book tape now flows. H-MAPENTRY1 is formally closed on your approved
registry rows, `p_map` is retired as a fair-value estimator, and the successor
lane (H-CROSSVENUE1) has a written design, a stamped holdout boundary, and a
working, orientation-checked market join. Four separate capture defects were
fixed, one of which had silently disarmed its own watchdog.

---

## What changed, and the evidence

### 1. Intl CS2 discovery — the big one

`pm_discovery.fetch_esports_catalog()` paged `/events?closed=false` up to
`offset<2000`, filtering esports client-side. Gamma hard-caps offset paging
(`offset=2500` → `"offset too large, use /events/keyset"`) and the unfiltered list
is dominated by politics and mainstream sports — 4,113 `sports`- and 3,109
`politics`-tagged rows in our own 10,223-row catalog on 09-06. CS2 sorted below
the reachable ceiling permanently.

**This is the same bug class already diagnosed and fixed on the PMUS side and
never ported** (`us_mapping.py:8-13`: *"CS2 is buried under MLB/NFL… silently
starved the first watch lane"*).

| | before | after |
|---|---|---|
| CS2 match events in the catalog | **0, ever** | 83 in 5 min |
| CS2 winner-book price changes | 0 | 27,330 in 10 min, 134 markets |

### 2. Winner-first asset selection

A CS2 event carries ~11 markets; only ~4 are winner books. The rest are Games
Total, Map Handicap and Odd/Even-Total-Kills props. Measured live: **465 CS2 match
events, of which only 139 have any OPEN winner market** — the other 326 are
finished matches (one slug dated **2026-03-10**) still in gamma's `closed=false`
list because their odd/even books stay open, holding subscriptions. 616 open
winner tokens against 2,402 open prop tokens: **80% of CS2 tokens were
untradeable**. Now winner-first: 488/488 winner tokens subscribed, **82/82 CS2
winner events fully covered** (was 56/79).

### 3. GRID poller — four defects, TDD + mutation-checked

- **Batch abort.** Only `GraphQLError` was caught, so one series' DNS failure
  aborted every other due series — the day's three worst gaps (**216.2s / 212.7s /
  208.8s, simultaneous**), 68 incidents in one day.
- **Sequential polling.** Batches up to 20 deep; the last series waited ~5s before
  its request was sent. Now `asyncio.gather`, ceilings unchanged.
- **A watchdog that disarmed itself.** `_prune` popped a series, then
  `discover_once`'s `setdefault` reinitialised it with `last_hash=""` — so the next
  hash compare *always* read as "changed", resetting `last_change` and
  `stale_flagged`. **A frozen series therefore defeated both the 180s stale alarm
  and the 6h frozen-prune indefinitely.** This is why the dota zombie survived.
- **Empty-response breaker.** Two series returned `data:{}` on all 291 of their
  polls with no prune condition able to fire.

Achieved cadence was p50 13.83s / p99 31.79s / max 216.23s against a 12s target;
the first two fixes remove essentially the whole tail.

### 4. Evidence-flow monitoring

Every prior alarm was process-liveness or directory-mtime. `data/pmus/books-*` was
checked by **nothing**, and can never go mtime-stale during a freeze because a
heartbeat is written every 30s regardless. `scripts/evidence_qa.py` adds three
ratio-normalised checks. It caught a real defect in itself: it labelled 09-05
`venue_quiet` when the recorder had actually been **down**, because heartbeat
density over the hours it ran was *higher* than baseline. Now checks observed span
first and reads `capture_broke`.

### 5. H-MAPENTRY1 closed (your approved rows, registry 39 → 42)

- `EXEC1-NOTE3` — the calibration gate **fails** (UPPER recall 0.4615 vs the 0.6
  bar). The terminal look was **not** taken, even though the FAIL criterion was
  separately satisfiable, because a verdict from an uncalibrated instrument is not
  evidence.
- `NOTE10` — **a maker strategy cannot be validated on a BBO tape.** A BBO tape
  cannot separate a sale through our level from a cancel of it, nor see queue
  position. Binding on every future resting-entry lane.
- `CLOSE1` — closed for *no path to a verdict*, explicitly **not** a falsification.

### 6. H-CROSSVENUE1 — the successor lane

Swap the **estimator**, not the model: use the deep intl CLOB as fair value,
execute on the PMUS match book, use GRID's 13–27s lead to pull/skew before
informed flow arrives. `q_round` is retained for a new job — *is this repricing
informed?* — not *what is this worth*.

Join verifier built and live-verified: **28 joined match markets, all aligned**,
team names identical on both sides. The same run on 09-06 joins **zero**, because
no intl CS2 winner market existed in that day's capture — the discovery fix is
what unlocked the lane.

Holdout boundary stamped **2026-09-07T06:39:27Z**. No basis, lead or edge has been
measured — deliberately, since the hypothesis is not pre-registered yet.

---

## Corrections to the record

Three things I told you earlier did not survive verification, and one prior
project conclusion was wrong:

1. **"~44 CS2 markets on intl"** — wrong. Zero CS2 *match* markets were ever
   captured; the 44 were whatever the broken sweep happened to reach.
2. **"Wasted polls cost us latency"** — wrong. The 180/min bucket peaks at 60/min
   (33%). The bottleneck was code serialization.
3. **The "PMUS venue collapsed 78.0 → 2.7 → 23.6, venue-side" conclusion** in
   `docs/learnings/2026-09-06.md` is **contaminated**. `gridbot-pmus-books` did not
   start until 17:58:48Z on 09-05; that tape spans **6.0h** against 23.7h on 09-06.
   A six-hour day was compared against full days. The map-vs-match comparison is
   unaffected and still holds.

Two audit recommendations were rejected on evidence:
- **`StartLimit`** — `RestartSec=10s` against a 10s/5-start window makes the burst
  limit arithmetically unreachable. Non-issue.
- **`WatchdogSec`** — every unit is `Type=simple` / `NotifyAccess=none` with no
  `sd_notify` anywhere, so a watchdog would **kill capture on a timer**. Do not add
  it. Evidence-flow monitoring is the watchdog.

---

## ACTION ITEMS

### A1 — Disk. 29-day fuse. *(highest priority)*

The intl tape now grows at **~23 GiB/day** (was 0.5–1.1). 681 GB free ⇒ **~29
days**. The volume is legitimate — **73% of it is CS2 winner markets**, exactly
what the strategy needs — so the fix is retention, not capturing less.

`scripts/backup_raw.py` already gzips finished day files offsite but **never
compresses or prunes locally**, and this data gzips **10.0×**. Compressing
finished local day files turns 29 days into ~290.

I did **not** implement it: `iter_jsonl` is the single tolerant reader, but
`engine.py`'s tail-cap seek reader and `scripts/_jsonl.py` are bespoke, and a
half-migrated read path means an instrument silently reading zero rows. Wants a
deliberate pass, not an unattended 1am one. **Your call on approach.**

### A2 — Push the branch

4 commits on `capture/latency-and-evidence-flow`. I don't push without you.

### A3 — Pre-register H-CROSSVENUE1 (decision C1)

The join is verified, so the row can now be drafted. Sequence: I draft → you
register → holdout accrues. It is **not** registered and nothing measures edge
until you do.

### A4 — Pre-match coverage (decision C3)

PMUS lists a match a median **4.46h** before scheduled start (p25 1.7h, p75 16.5h,
50 matches/day) and the recorder only starts at GRID `started`. We capture **none**
of it — and that is where a thin book is most mispriced. Costs book-poll budget.

### A5 — Trades tape

`subscribe_trades` exists in the vendored SDK and `NOTE10` makes it the
prerequisite for any maker lane. You approved the Ed25519 credentials. Strongest
guardrail found: **`MarketsWebSocket` has zero order surface** — no
`order/place/cancel/submit/buy/sell` attribute exists — so the recorder is
structurally incapable of trading. Build status is in the addendum below.

### A6 — Codebase cleanup

Audit was running at write time; results in the addendum.

---

## Still true, unchanged

- `dry_run=True`. No orders placed. Rail 3 intact — arming is your act alone.
- Capture runs; `Linger=yes` now, so it survives a reboot for the first time.
- No strategy has a pre-registered PASS. Nothing is armed or near arming.
