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

## 3. THE BLOCKING FINDING: we cannot see the book we would trade

Of the 14 joined match markets per day, PMUS book coverage exists for almost
none of them:

| day | joined | booked that day | booked another day | **never booked, any day** |
|---|---|---|---|---|
| 20260907 | 14 | 3 | 2 | **9** |
| 20260908 | 14 | 1 | 0 | **13** |

These are not markets that had yet to start. They were never captured at all,
and they include the most liquid matches on the slate:

```
aec-cs2-g2-ast-2026-09-08        G2 vs Astralis
aec-cs2-gl-furia-2026-09-08      GamerLegion vs FURIA
aec-cs2-all-faze-2026-09-08      FaZe
aec-cs2-big-bb-2026-09-08
```

**Mechanism, already named in design §10 and never fixed:**
`LiveTracker.live_maps()` (`pmus_books.py:87-102`) gates on GRID `started`, so
PMUS books are recorded only from kickoff — while PMUS lists a market a median
**4.46h** before scheduled start. `MAX_SLUGS_PER_CYCLE=8` against up to 27
concurrent slugs compounds it. The pre-match window, which the design identifies
as where a thin book is *most* mispriced, is entirely uncaptured.

### Why this is sequencing-critical, not a detail

H-MAPENTRY1 died of exactly this. Its holdout needed 60 deduped qualifying
entries and accrued **39** across its whole window — no path to a verdict, and
the lane closed without ever being falsified (`H-MAPENTRY1-CLOSE1`).

The design argued CROSSVENUE1 escapes that: *"This lane sees more events in one
night"* than MAPENTRY1's entire holdout. On the measured coverage it does not.
The observable population is **1–3 markets/day**, not 14 and not 790–1,645
round-ends. **A holdout pre-registered against today's capture would be starved
by construction**, and would produce another INCONCLUSIVE lane closure eighteen
days from now.

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

1. **Close the pre-match PMUS capture gap** — subscribe books from listing, not
   from GRID `started`; raise or window `MAX_SLUGS_PER_CYCLE`. *This is now the
   gating item, exactly as the intl discovery fix was before it.* Until it ships,
   every day of holdout accrues 1–3 observable markets instead of 14.
2. Let coverage accrue and **re-measure the observable rate** — the starvation
   clause needs a real number, and today's cannot be it.
3. Classifier + basis instrument on training data only (pre-boundary).
4. Registry row drafted → **the user registers** → holdout accrues.

Steps 1–3 produce no PnL claim. Rail 3 is unchanged: `dry_run=True` until a
pre-registered PASS, and flipping it live is the user's own act.

## 6. Stated limits

- No basis, lead, or edge is computed anywhere here. Coverage, orientation and
  observability only.
- Two days of intl CS2 data is not a rate. The 14/day join count and the 1–3/day
  observable count are both provisional and must be re-measured after the capture
  fix.
- One correctly-resolved inversion is evidence for the resolver, not a
  measured accuracy. §7's refusal test remains the load-bearing guarantee.
- The holdout boundary (`2026-09-07T06:39:27Z`) is untouched: nothing here reads
  post-boundary outcomes.
