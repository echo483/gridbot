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

### Cause B — GRID never discovered the series: NOT the discovery window

> **Second correction (2026-09-08, later the same session).** This section
> previously blamed `config.DISCOVERY_WINDOW` being −4h/+1h against the mapper's
> −6h/+30h, and recommended widening it. **That was wrong, and the recommendation
> is withdrawn.** Widening the window would have spent rate budget and fixed
> nothing. The correct attribution is below. The error came from reading the
> window backwards — see "How the mistake was made".

The poller's sweep at time *t* asks GRID for series scheduled in *[t−4h, t+1h]*.
So a series scheduled at *S* is discoverable by any sweep run in **[S−1h, S+4h]**
— a five-hour opportunity window, about twenty 15-minute sweeps. Testing every
mapped-but-never-discovered series against the actual sweep timestamps
(`scripts/discovery_attribution.py`):

| | 20260906 | 20260907 |
|---|---|---|
| mapped series / of those discovered | 50 / 43 | 42 / 28 |
| opportunity window contained **no sweep at all** → capture was DOWN | **0** | **7** |
| sweeps ran in the window, series still not returned → real window gap | **0** | **0** |
| window opens after the day ended (correctly untracked) | 7 | 7 |

**Category B is zero on both days, and on 09-06 there is no loss at all.** Every
series the poller missed was missed because the poller was not running. The
window width never bound.

The "discovered" and "not yet due" columns drift as the live tape advances — a
series that is legitimately not-yet-tracked today is discovered tomorrow, and
re-running the script moves it between those two columns. The two columns that
carry the finding, capture-down and real-window-gap, are stable.

The outage is already known and already fixed: the host suspended
**04:31:59–11:47:24 PDT on 2026-09-07** (7h16m), which is exactly the 440-minute
hole in `discovery-20260907.jsonl` and the 435-minute hole in the `series_state`
tape of the same day. It is the fourth of the four outages the capture unit's own
comment records (`08-25 10.8h, 09-02 7h48m, 09-05 13h01m, 09-07 7h26m = 39+
hours`), and the `--what=sleep:handle-lid-switch` inhibitor added afterwards is
the fix. `systemd-inhibit --list` confirms both locks are held now.

### How the mistake was made — and the instrument that permitted it

The discovery tape wrote **only per-series rows**. A sweep that returned nothing
wrote nothing, so "the sweep ran and found an empty window" and "the sweep never
ran" were the same observation: silence. Row timestamps were the only evidence of
uptime, and inferring uptime from them is what produced the wrong cause.

This is the identical defect `scripts/evidence_qa.py` check 4 was added to close
earlier the same night, in a different tape. Fixed here at the source:
`grid_poller.discover_once` now appends one `discovery_sweep` row per sweep per
title — always, including zero-result and failed sweeps — carrying the window it
queried, the series and page counts, an `ok` flag and the tracked total. Capture
uptime becomes directly observable instead of inferred. (Dormant until the user
restarts capture — rail 4.)

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

1. **Close the ONE real capture gap.** Subscribe PMUS books from listing rather
   than from GRID `started` — landed this session behind
   `config.PMUS_PREMATCH_BOOKS`, **default off**, because it is a rate-budget
   decision (see §6). Do **not** widen `config.DISCOVERY_WINDOW`: measurement
   says it never bound.
2. **Protect uptime, because that is what actually cost the markets.** The
   `discovery_sweep` marker makes blackouts visible. The suspend door is already
   shut; the remaining exposure is that nothing yet *alerts* on a mid-day hole.
3. Let coverage accrue and **re-measure the observable rate** — the starvation
   clause needs a real number, and today's cannot be it.
4. Classifier + basis instrument on training data only (pre-boundary).
5. Registry row drafted → **the user registers** → holdout accrues.

Steps 1–3 produce no PnL claim. Rail 3 is unchanged: `dry_run=True` until a
pre-registered PASS, and flipping it live is the user's own act.

## 6. The one decision left for the user: pre-match book budget

`config.PMUS_PREMATCH_BOOKS` (default **off**) makes `pmus_books` poll the match
book of a mapped series GRID is tracking but that has **not started**. No new
time source is read: GRID only tracks a series inside `DISCOVERY_WINDOW`, so
"tracked and not started" already means "starting soon".

The venue's rate ceiling is not the binding cost. Measured 2026-09-07 from the
recorder's own stats lines: **53.96 polls/min = 0.9 req/s**, against the
recorder's own ~4 req/s design cap and Polymarket's 20 req/s. There is headroom.

The binding cost is **resolution**. `MAX_SLUGS_PER_CYCLE` is a round-robin cap,
so adding candidates does not add polls — it *divides* the same polls among more
slugs. Observed concurrent candidates on 09-07 were 0–11; pre-match would add
roughly one slug per tracked-not-started mapped series. Doubling candidates
halves each slug's poll rate, and NOTE11 is a finding about exactly that
resolution.

So the flag is deliberately only half the change. The other half — raising
`MAX_SLUGS_PER_CYCLE`, which the 0.9 req/s measurement says is affordable — is
left to the user because it is the part that actually spends budget. Pre-match
specs are appended **after** live specs in `slugs_for`, so if the cap does bind,
the round robin starves the pre-match tape before the in-play one.

Rail 4: this is dormant until the user restarts `gridbot-pmus-books`. Nothing in
this session restarted any capture unit.

## 7. Stated limits

- No basis, lead, or edge is computed anywhere here. Coverage, orientation and
  observability only.
- Two days of intl CS2 data is not a rate, and only one of those days is
  complete. The 14/day join count and the 5/day observable count are provisional
  and must be re-measured after the capture fixes — on several full days.
- The cause attribution now rests on two days (09-06, 09-07) and is
  categorical, not a ratio: capture downtime explains **all** never-discovered
  series on both, and the discovery window explains **none**. Two days is still
  two days — the claim is that the window was not a cause on the days measured,
  not that it can never be one.
- One correctly-resolved inversion is evidence for the resolver, not a
  measured accuracy. §7's refusal test remains the load-bearing guarantee.
- The holdout boundary (`2026-09-07T06:39:27Z`) is untouched: nothing here reads
  post-boundary outcomes.
