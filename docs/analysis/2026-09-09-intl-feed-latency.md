# The intl feed: 83ms at the median, 31s in the tail — and it is not what picks us off

**2026-09-09 · overnight loop · training series only · read-only, no edge claim**

Registry provenance: pre-registration support for `H-CROSSVENUE1-PRENOTE1`;
the fill-channel context is `H-MAPENTRY1-NOTE11` / `H-MAPENTRY1-NOTE12`.

Reproduce: `uv run python scripts/intl_feed_latency.py --intl-jsonl FILE... --by-label`

The cross-venue lane rests on acting inside the window between an intl repricing
and PMUS following it. PMUS follows at a **median 35.8s**. So: how much of that
window does our own ingest spend? Every intl WS `price_change` carries the venue's
millisecond timestamp next to our receive clock, so this is measured, not inferred.

## 1. The bulk — 7,092,340 messages

| quantile | venue stamp → our receive |
|---|---|
| p10 | 77.5 ms |
| **p50** | **83.3 ms** |
| p90 | 99.1 ms |
| **p99** | **3,055.9 ms** |
| **p99.9** | **31,510.3 ms** |
| max | 56,557.7 ms |

**The core is excellent and the tail is not.** Against a 35.8s reaction window,
83ms is nothing — we spend **0.2%** of the window learning what happened. But 1%
of messages arrive more than 3 seconds late and 0.1% more than **31 seconds**
late, which is the whole window gone.

That tail is a capture-health item in its own right: whatever causes it
(reconnects, backpressure) is invisible to the current health checks, which watch
for a tape going *silent*, not for one running *late*.

**Clock-skew limit on the absolute number.** We do not sync to the venue's clock,
and **2.51%** of messages arrive "before" their venue stamp — impossible in
physics, so that share bounds the skew rather than being explained away. The
absolute 83ms should be read as "sub-100ms, with a caveat", not as a calibrated
figure.

## 2. The reading that matters — latency by what PMUS did

If INFORMED events (PMUS moved *first*; we get picked off) ran systematically
late, the −6.5pt fill-selection cost would be **our** latency, and faster
infrastructure would fix it.

| label | n | p50 | p90 | p99 | >1s | >5s |
|---|---|---|---|---|---|---|
| stale | 121 | 82.1 ms | 91.4 ms | 3,185 ms | 1.7% | 0.8% |
| **informed** | 44 | **82.5 ms** | 105.2 ms | 34,100 ms | 4.5% | 4.5% |
| no_follow | 199 | 81.1 ms | 95.7 ms | 4,626 ms | 1.5% | 0.5% |

**The medians are identical.** 82.5ms on the events that picked us off against
82.1ms on the ones that did not. Constant clock skew cancels out of this
comparison, which is exactly why it carries the conclusion the absolute number
cannot.

**So the pick-off is not our slowness.** It is genuine adverse selection — someone
on the thin venue knew first — and **no amount of infrastructure fixes it**. For
a lane heading toward live capital that is the useful form of the finding: the
12.1% INFORMED rate is a cost to be priced, not a bug to be engineered away.

## Stated limits

- **The tail comparison is not evidence.** INFORMED shows 4.5% of events over 1s
  against 1.7% for STALE, which is the direction you would expect — but 4.5% of
  44 is **two events**. It is reported because suppressing it would be worse, and
  it should not be leaned on.
- The median comparison is the well-powered one (n=364 across 7 usable books) and
  is where the conclusion comes from.
- Latency is measured on the message carrying the move, not on any order we
  placed; there is no order path here and no PnL anywhere.
