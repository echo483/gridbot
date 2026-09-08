# H-CROSSVENUE1 readiness: the join works, the execution venue is invisible

**2026-09-08 · overnight loop · read-only. No basis, no lead, no edge — the lane
is not pre-registered (iron rail 2) and nothing here measures its expectancy.**

Reproduce: `uv run python scripts/crossvenue_coverage.py`

## 1. The discovery fix delivered

Design §10 recorded the lane as blocked: zero CS2 match markets had ever been
captured on intl. Fixed 2026-09-07 (tag-scoped route). Confirmed:

| day | intl CS2 winner markets (match, map) | PMUS mapped series | joined |
|---|---|---|---|
| 20260904 | 0, 0 | 87 | 0 |
| 20260905 | 0, 0 | 73 | 0 |
| 20260906 | 0, 0 | 100 | 0 |
| **20260907** | **110, 218** | 98 | **14** |
| **20260908** | **75, 150** | 84 | **14** |

The blocking finding is cleared. Intl CS2 data now accrues.

## 2. The orientation resolver was validated on a real inversion

Design §7 calls the join "the highest-risk component": *"If intl's Yes resolves
to team A and PMUS's Yes to team B, every number is exactly backwards and looks
excellent."* On 2026-09-08 the first genuine inversion appeared, and the resolver
caught it:

```
intl question : Counter-Strike: BIG Academy vs MORROW (BO3) - EPL Series 10 …
intl YES team : BIG Academy
pmus slug     : aec-cs2-mor-biga-2026-09-08
pmus YES team : MORROW            ->  orientation = INVERTED
```

**It is a real inversion, not a defect**, and an independent signal agrees: the
PMUS slug itself is ordered `mor-biga` — MORROW first — while intl titles BIG
Academy first. The resolver reads the `sides` map and never looks at slug order,
so those are two independent derivations landing on the same answer.

This is the first live evidence that `intl_price_as_pmus_yes` is being handed
correct orientations. It does not retire the risk (one case is not a rate), but
the component now has a real positive.

## 3. THE BLOCKING FINDING: we mostly cannot see the book we would trade

> **Correction (same session).** An earlier revision of this section reported
> "9 of 14 (09-07) and 13 of 14 (09-08) never booked on any day" and attributed
> all of it to `pmus_books` subscribing from GRID `started`. Both were wrong.
> The counts folded in matches that had not yet been played, and the attribution
> named one subsystem when there are two. Corrected below; the conclusion that
> the observable population is materially smaller than the join count survives,
> at a less severe magnitude.

Decomposed properly. 2026-09-08 is a partial UTC day (~6h old), so most of its
slate had simply not been played yet; **2026-09-07 is the meaningful sample**:

| outcome for a joined market | 20260907 | 20260908 (partial) |
|---|---|---|
| **booked — we saw the execution venue** | **5** | 1 |
| GRID polled the series, PMUS book blind | 3 | 1 |
| GRID never discovered the series at all | 3 | 11 |
| future match, not yet due (not a loss) | 3 | 1 |

**Observable rate on the fuller day: 5 of 14 (36%).** Of the 6 genuine losses,
the causes split evenly between two independent subsystems.

### Cause A — PMUS books blind on a series GRID *was* polling (3 of 14)

`LiveTracker.live_maps()` (`pmus_books.py:87-102`) gates on GRID `started`, while
PMUS lists a market a median **4.46h** before scheduled start (design §10).
`MAX_SLUGS_PER_CYCLE=8` against up to 27 concurrent slugs compounds it. The
pre-match window the design identifies as *most* mispriced is uncaptured.

### Cause B — GRID never discovered the series (3 of 14, same-day matches)

Verified against `data/grid/discovery-*.jsonl`: these series were **never seen**,
not tracked-then-pruned. The mechanism is a window mismatch between two GRID
queries that are supposed to describe the same slate:

| consumer | scheduled-time window |
|---|---|
| `grid_poller` discovery (`config.DISCOVERY_WINDOW`) | **−4h to +1h** |
| PMUS mapper (`us_mapping.fetch_grid_series`) | **−6h to +30h** |

The mapper therefore knows about series the poller has not tracked. For a future
match that is correct by design. But the poller's **1-hour forward horizon** means
a series must be caught in roughly the four 15-minute sweeps before it starts —
and a rescheduled start, a drifted start time, or a sweep that misses it loses the
series for good (`_prune` then drops "never started and scheduled >12h ago").
Three same-day 09-07 matches were lost exactly this way.

This is the same bug class the repo has already paid for twice: a scoped sweep
that silently starves a lane. `us_mapping.py:8-13` documents the PMUS instance
("CS2 is buried under MLB/NFL... silently starved the first watch lane") and §10
above documents the intl one.

### Why this is sequencing-critical

H-MAPENTRY1 died of exactly this arithmetic. Its holdout needed 60 deduped
qualifying entries and accrued **39** across its whole window — no path to a
verdict, and the lane closed without ever being falsified
(`H-MAPENTRY1-CLOSE1`).

The design argued CROSSVENUE1 escapes that: *"This lane sees more events in one
night"* than MAPENTRY1's entire holdout. At **5 observable markets/day** that
claim is not yet supported — and 6 of the 14 losses per day are fixable capture
blindness, not market structure. **A holdout pre-registered against today's
capture accrues at roughly a third of the rate the design assumed.**

## 4. What the fill channel changes for clause (iii)

Design §12 anticipated this and it landed tonight: with prints, clause (iii) no
longer has to *infer* fills from a BBO tape. But the same night's audit found the
BBO channel is not merely imprecise — it is misspecified, and the EXEC1 bracket
does not contain the truth (`H-MAPENTRY1-NOTE11`,
`docs/analysis/2026-09-08-fill-model-misspecification.md`). Consequences for this
lane, both favourable:

- clause (iii) must be built on `gridbot.analysis.trade_prints`, never on
  `exec_fills` alone. The design's instruction to reuse EXEC1's machinery stands
  only for its *calibration-gate discipline*, not for its observation channel.
- the `-6.5pt` adverse-selection cost that killed the last lane becomes directly
  measurable: every print names its aggressor (`maker.intent` / `taker.intent`),
  so "we fill when wrong" is observable rather than inferred.

## 5. Recommended sequencing (a change to design §11)

The design's build order has the registry row at step 6, after the classifier.
The coverage measurement says one item must move ahead of it.

1. **Close BOTH capture gaps — they are gating, and they are independent.**
   (A) subscribe PMUS books from listing rather than from GRID `started`, and
   raise or window `MAX_SLUGS_PER_CYCLE`; (B) widen `config.DISCOVERY_WINDOW`'s
   forward horizon so the poller's slate matches the mapper's, or seed the
   tracker from the PMUS mappings it already reads. Each costs ~3 of 14 markets
   a day. Fixing only one leaves roughly half the loss in place.
2. Let coverage accrue and **re-measure the observable rate** — the starvation
   clause needs a real number, and today's cannot be it.
3. Classifier + basis instrument on training data only (pre-boundary).
4. Registry row drafted → **the user registers** → holdout accrues.

Steps 1–3 produce no PnL claim. Rail 3 is unchanged: `dry_run=True` until a
pre-registered PASS, and flipping it live is the user's own act.

## 6. Stated limits

- No basis, lead, or edge is computed anywhere here. Coverage, orientation and
  observability only.
- Two days of intl CS2 data is not a rate, and only one of those days is
  complete. The 14/day join count and the 5/day observable count are provisional
  and must be re-measured after the capture fixes — on several full days.
- The cause split (3 / 3) rests on a single complete day. It is enough to
  establish that BOTH subsystems lose markets; it is not a stable ratio.
- One correctly-resolved inversion is evidence for the resolver, not a
  measured accuracy. §7's refusal test remains the load-bearing guarantee.
- The holdout boundary (`2026-09-07T06:39:27Z`) is untouched: nothing here reads
  post-boundary outcomes.
