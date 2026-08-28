> **Provenance:** produced by the `verify` lane (Claude Opus 5, effort xhigh,
> $0.77, 410s) against the day's src/ diff plus real captured row shapes.
> Claude Code then checked each money-critical finding against live data:
> **#5 and #6 CONFIRMED** (see below), **#7 REFUTED** — slate rows do carry
> `a`/`b` and a real p_map (0.7974 sampled), so they do not price at 0.5.
> Remaining findings are unverified as of this writing — treat as claims.

**Verified against data/shadow/mapentry-20260828.jsonl (1,878 rows):**
- 109 slate rows (`src_hypothesis_id` = H-MAPENTRY1-SLATE); **15 of them
  QUALIFY**, and every row carries `hypothesis_id: H-MAPENTRY1` — the
  executor cannot distinguish a slate row from a detector-trigger row, so the
  pre-registered entry population widened silently (finding #6).
- Registry H-PRICEDIN1 freezes the licensing bar at **+1.0pt oriented drift
  at 120s** and states no bucket is licensed (measured 0.50/0.63/0.94pt). The
  engine gate requires `DRIFT_MIN_CONFIRM = 0.005` — 0.5pt **cumulative over
  3 samples** — i.e. looser than the frozen bar in both threshold and
  construction (finding #5).

# ADVERSARIAL VERIFICATION — 2026-08-28 trade-path diff (85cbafd..HEAD)
**Lane:** verify · **Scope:** drift gate, band revert, slate coverage · **Repo access:** none — judged from the bundle only

---

## SUMMARY

The drift gate's *plumbing* is materially better than the near-miss version: it fails closed on empty/partial/non-finite, it filters the horizon, it filters the side, it accepts both field spellings. But the near-miss class is not one bug — it is **"a value crossed a producer/consumer boundary and nobody checked it against real capture."** The field *names* got fixed; the **value domains did not**. Three of them are unverifiable from this bundle and each one, if wrong, is either a 100% silent outage or a 100% silent pass.

Separately, the gate's threshold does not implement the frozen bar — it is one tick of price resolution — and the slate-coverage change quietly routes a new, unregistered entry population into the live order path.

---

## FINDINGS

### 1. BLOCKER — `DRIFT_MIN_CONFIRM = 0.005` is one half-tick, not "0.5pt of confirmation"; the gate is a sign test
`src/gridbot/executor/intents.py` ~L147: `DRIFT_MIN_CONFIRM = 0.005  # 0.5pt cumulative oriented move in our side's favor`

The authoritative postquote row shows `"bid":0.51,"ask":0.65,"mid":0.58` — a **1-cent tick book**, so `mid = (bid+ask)/2` is quantized to multiples of **0.005**. The smallest representable non-zero `oriented_move` is therefore exactly `0.005`. `sum(3 moves) >= 0.005` is arithmetically identical to `sum(3 moves) > 0`.

The gate does not require the market to have "STARTED moving our way" by any measurable amount. It requires the 3-sample sum to be *not ≤ zero*. Under the measured population (mean oriented drift +0.50..+0.94pt per sample), the expected 3-sample sum is **+1.5..+2.8pt against a +0.5pt bar** — the gate passes on the *unconditional average case*. My rough estimate is a 60–70% admit rate, i.e. decoration, not a filter.

**Resolve deterministically:** from the existing n≈7,900 study, compute the empirical `P(sum of 3 consecutive same-slug/side 120s moves ≥ 0.005)`. If that is >0.5, this gate does not gate. Print it before arming.

### 2. BLOCKER — the units of `oriented_move` are unverified, and the only real sample is `0.0`
`engine.py` ~L128 `m = row.get("oriented_move")` → compared against `0.005` in `intents.py`.

The registry speaks in **points** (`+0.94pt`, `+1.0pt bar`). The code's threshold is in **probability units** (`0.005`). The one captured row has `mid0=0.58, mid=0.58, oriented_move=0.0` — which is consistent with *either* convention and therefore discriminates nothing.

- If the sampler writes probability units, `0.005` = 0.5pt (finding 1 applies).
- If the sampler writes **points** (`0.94` for 0.94pt), then `0.005` is a threshold **200× too small** and typical sums are `+2.0` — the gate passes on essentially every non-negative window, permanently.

This is precisely the near-miss shape one level down from the field name. **Check:** grep the 08-28 capture for `postquote` rows with `mid != mid0` and confirm `oriented_move == ±(mid - mid0)` in probability units, not `100×` that.

### 3. BLOCKER — short-side orientation is asserted in a docstring and unverified anywhere in the bundle
`engine.py` ~L60 (`SIDE:` docstring: *"`oriented_move` is signed with respect to the side that was TAKEN at sample time"*); consumed by `drift_confirmed`, which treats **positive = favorable** for both sides.

If the sampler actually computes `mid - mid0` (unoriented) and merely *labels* the row with `us_side`, then for every `us_side="short"` candidate the gate confirms exactly when the market is moving **against** us and blocks when it moves for us — a systematic adverse-selection filter on half the book, on an engine already at 3W-10L. The bundle contains no short row and no row where `mid != mid0`. The field name says "oriented"; nothing in the bundle proves it is.

**Resolve:** find any slug on 08-28 with samples on both sides in the same window and assert `oriented_move(long) ≈ -oriented_move(short)`. If they have the same sign, the sampler is unoriented and finding 3 is live. This is a two-minute query and it is the highest money-direction risk in the diff.

### 4. BLOCKER — slug *value* identity between producer and consumer is unverified; the near-miss fix only addressed the field *name*
`engine.py` ~L114: `if (row.get("slug") or row.get("market_slug")) != slug:` compared against `intent.market_slug`.

Accepting both spellings guarantees the loader reads *a* slug. It does **not** guarantee the string equals what `build_us_intent` puts on the intent. The sampler's `slug` is documented as *"the venue's own spelling"*; `intent.market_slug` comes from the pmus mapping. The only real mapentry row in the bundle has `"market_slug": null` (`skip_reason: no_pmus_mapping`) — **there is no mapped example in this bundle**, so I cannot verify the two strings are byte-equal. Note the venue slug embeds a date (`...-2026-08-27-map2`); a date derived differently on the two sides is a 100% silent, fail-closed outage, identical in effect to the bug caught this morning, and identical in test-visibility (fixtures on both sides would agree).

**Resolve:** take one real 08-28 mapentry row with a non-null `market_slug`, and assert an exact string match against at least one real postquote `slug` for the same series/map.

### 5. BLOCKER — the executor gate is cited as licensed action; the registry row shown does not license it, and its threshold contradicts the frozen bar
`engine.py` ~L276 (`# DRIFT-CONFIRMATION gate (priced-in analysis 2026-08-28, licensed action 2)`) and `intents.py` ~L157 (same phrase).

The H-PRICEDIN1 row's item (2) registers **`scripts/priced_in_test.py` as a diagnostic instrument** with a **FROZEN BAR of mean oriented drift > +1.0pt at 120s**, and states plainly that **no discount bucket is licensed as of registration**. It does not, in the text provided, license an executor gate, and it does not authorize a +0.167pt/sample proxy (sum of 3 ≥ 0.005) in place of a +1.0pt mean.

Two consequences:
1. **No code in this diff implements the frozen bar.** There is no bucket-licensing check anywhere. If "no bucket is licensed", the executor's correct behavior is to place nothing in any discount bucket — arming with only band+drift places into buckets the registry says are unlicensed.
2. **The falsification criterion is contaminated.** H-PRICEDIN1(a) is stated over *"the reverted (0.50,0.80) band"* population. Adding an unregistered, unpowered selection rule to the live entry path means night-5 fills are drawn from neither the registered population nor the study population — they cannot falsify or confirm the hypothesis as written. Either pre-register the gate as an amendment (with its threshold, window, horizon, and expected admit rate) before collecting data under it, or don't ship it into the armed path.

If the truncated registry text does license this gate with these constants, quote that clause — I judged only what the bundle contains.

### 6. BLOCKER — slate-coverage rows reach the live order path as a new, unregistered entry population
`mapentry_recorder.py` ~L71 `handle_slate(...)` → `return self.handle(instance)`; `engine.py` ~L180 `self.follow_us = Follow(config.DATA_DIR / "shadow", "mapentry", from_start=False)`.

`handle_slate` emits an instance for **every coherent round snapshot of every live cs2 series**, through the *same* `Recorder.handle` that does mapping lookup, BBO fetch and qualification, into the *same* `mapentry` day-file the executor tails. Nothing in this diff filters the executor on `src_hypothesis_id`. Unless `build_us_intent` rejects `src_hypothesis_id == "H-MAPENTRY1-SLATE"` (not shown in the diff, and its docstring says only *"one QUALIFYING mapentry row"*), then **every trigger-free round of every live series is now a placement candidate**, gated only by band + the gate in finding 1.

Order-of-magnitude: trigger-driven entries are a handful per night; slate is ~1 per round × ~24 rounds × maps × live series. This is the largest change to trade-path exposure in the diff and it arrived inside a "coverage" commit.

**Resolve before arming:** either show the executor-side `src_hypothesis_id` filter, or write slate rows to a separate stream (`slate-<day>.jsonl`). Do not rely on `qualifies` alone.

### 7. MAJOR — `slate_instance` omits `a`/`b`; if `handle()` reads them directly, every slate row prices at p_map ≈ 0.5, inside the armed band
`mapentry.py` ~L60-79: the returned dict carries `state.ct.score`/`state.t.score` but **no `a`, `b`** keys, while the real mapentry row carries `"a":4,"b":0`.

If `Recorder.handle` derives a/b from `instance["state"]`, fine. If it reads `instance.get("a", 0)`, every slate row gets `p_future(0,0) = 0.5` → `p_map ≈ 0.5` → **passes `us_band_gated((0.50,0.80))` at the boundary** and becomes a placement candidate with a fabricated fair value. I cannot see `handle()` in this diff. This is a silent-corruption path with a money consequence; the fixture-based tests would pass either way if the fixture instances also omit a/b.

### 8. MAJOR — slate rows corrupt the H-MAPENTRY1 denominator: same row type, same `hypothesis_id`, double-counted, ct-only, no model-version stamp
`mapentry.py` ~L35 `SLATE_HYPOTHESIS_ID = "H-MAPENTRY1-SLATE"` is placed on the **instance**, but the real emitted row shows `"hypothesis_id":"H-MAPENTRY1"` with the source in `src_hypothesis_id`. So every existing analysis keyed on `hypothesis_id == "H-MAPENTRY1"` silently absorbs the slate population starting today. Specifically:

- **Double counting:** a trigger instance (`dedup_extra:"t:0.65"`) and a slate instance (`dedup_extra:"slate"`) for the same (series, map, round) are *different* dedup keys, so the same market-moment lands twice with different sides.
- **Side asymmetry:** slate is hard-coded `"side": "ct"`. "Derivable by symmetry" holds for `q`, **not** for qualification — qualification depends on the *ask on a specific side's token*, and the t-side book is never evaluated. Any qualification-rate computed over slate rows has half its space missing.
- **q=None rows:** `mapentry_recorder.py` ~L175 wraps `load_served()` in `try/except` and logs a warning; on failure `rec.slate = None` and **every series still emits a slate row with `q=None`**, all necessarily non-qualifying. That silently deflates the measured qualification rate for the whole night, with a single WARNING line as the only signal.
- **No provenance:** no model version/artifact hash on the slate instance, and I cannot verify `load_served()` here returns the same model version the trigger path uses. If it differs, calibration studies mix two models under one id.

**Recommendation:** emit slate rows to a separate file with `hypothesis_id = "H-MAPENTRY1-SLATE"` at the row level, and hard-fail the recorder (or skip slate emission entirely) when the predictor is unavailable rather than writing `q=None` rows into the denominator.

### 9. MAJOR — the drift gate has no recency bound; a confirmation can be up to ~48h and ~20 rounds stale
`engine.py` ~L86 `for day in (yday, day0)` … ~L138 `return moves[-n:]`.

Rows carry `ts_ns`/`src_ts_ns` and neither is read. If today's file has 1 matching sample and yesterday's has 100, the gate confirms on two day-old samples plus one fresh one. Within a single map, three samples from round 3 satisfy an entry at round 20 — a completely different state, on a book whose earlier drift was driven by information that has since resolved. "The market has STARTED moving our way" is a statement about *now*; this measures "at some point during this slug's life, the last three sampled windows summed positive."

Add a max-age (`now - src_ts_ns <= N minutes`) and, if the row carries it, a round-proximity bound.

### 10. MAJOR — the 1MB tail-cap makes the gate's admit rate a function of unrelated sampler volume, and can raise an uncaught `UnicodeDecodeError`
`engine.py` ~L92-96:
```
skip = max(path.stat().st_size - 1_000_000, 0)
with open(path) as fh:
    fh.seek(skip)
```
Two problems:

(a) At n≈7,900 rows/day × ~250B ≈ 2MB/day, **the cap triggers on a normal night**. Samples older than the last ~1MB are silently invisible, so a slug quoted early and crossed late fails closed for a reason nobody can see in the logs. As the sampler's volume grows, the gate's effective lookback shrinks — the admit rate drifts without any code change.

(b) `open()` in text mode with an arbitrary byte offset resets the decoder mid-character. A non-ASCII byte anywhere near the cut (team names, `dedup_extra`) raises `UnicodeDecodeError`, which is a `ValueError` — **not caught by `except OSError`** at ~L134. It escapes `recent_postquote_moves` into the placement path. I cannot verify from the bundle whether the engine's per-record handler catches broad exceptions; if it doesn't, this kills the executor tail mid-session.

Fix: `open(path, encoding="utf-8", errors="replace")`, and catch `(OSError, UnicodeDecodeError)`.

### 11. MAJOR — the day window only looks *backward*; a producer day-label ahead of the loader's UTC day is a total silent outage
`engine.py` ~L74-84: `day0 = today or f"{now:%Y%m%d}"` (UTC), `yday = day0 - 1`.

I cannot verify from the bundle what timezone the postquote sampler uses to name its files. If it is ever *ahead* of UTC (local-date naming in a UTC+n deployment, or a clock skew across processes), the loader reads `day0` and `day0-1` while the sampler writes `day0+1` → zero matches → 100% fail-closed skip, with clean logs and green tests. That is the exact failure the team just shipped and caught.

Cheap immunization: glob `postquote-*.jsonl`, sort, take the two most recent, and assert non-empty at startup with a loud log line (`drift source: <path> N rows dt_s==120`).

### 12. MAJOR — `handle_slate`'s four record-shape assumptions are unverified against real `series_state` capture
`mapentry_recorder.py` ~L74-82: `record.get("type") != "ss"`, `record.get("title") != "cs2"`, `(record.get("data") or {}).get("seriesState")`, `state.get("started")/state.get("finished")`.

The bundle provides authoritative row shapes for `postquote` and `mapentry` and **none for `ss`/`series_state`**. If `title` is nested (e.g. under `data.seriesState`), or the value is `"CS2"` / `"Counter-Strike 2"`, `handle_slate` returns `None` for every record and full-slate coverage records **zero rows** — while `log.info("full-slate coverage armed (roundwin-v1)")` prints, and every fixture-based test passes. This fails closed for money, but it produces a false coverage claim that a future denominator study would treat as real. Verify against one real `series_state` line before claiming the night-3 gap is closed.

### 13. MAJOR — sum-over-window with no sign consistency and no recency weighting
`intents.py` ~L179 `return sum(moves) >= min_confirm`.

`[+0.05, -0.04, -0.005]` confirms. The most recent two samples are adverse and the gate says "the market has started moving our way." The three samples are also serially correlated (same map, same book, same information flow), so `min_samples=3` does not buy three independent confirmations — it buys three views of one trend. If the intent is "started moving," require the most recent sample non-negative, or a majority-positive condition, and say so in the amendment.

### 14. MINOR — the drift gate is ordered *before* the opposite-side invariant, shadowing its accounting
`engine.py` ~L276-292: the drift block returns `"skipped:no_drift_confirm"` before the STATE-1e opposite-side check that traces to a real 08-27 live loss.

No money risk (both skip), but rows violating *both* now log only the drift reason, so the evidence log will under-count opposite-side-invariant hits — degrading the ability to validate the invariant that was written in blood. Evaluate the safety invariant first, or record both reasons.

### 15. MINOR — the skip row likely cannot reconstruct the gate's own selectivity
`engine.py` ~L283 `self._skip_row(intent, record, "skipped:no_drift_confirm", armed)`; the `moves` list appears only in the `log.info`, not in the decision row (`_skip_row`/`_decision_row` bodies not in the diff).

To measure whether this gate helped or hurt, the skip row must carry `moves`, `sum(moves)`, `n_samples`, `ask`, and `entry_price_max`. Without them the counterfactual "what would the band-gated population have done" is only reconstructable from logs. Add them — this is the data that answers finding 1 empirically after night 5.

### 16. NIT — comment contradicts the code's ordering
`intents.py` ~L145: `DRIFT_WINDOW = 3  # postquote samples the engine feeds in (most recent first kept)`. The loader returns `moves[-n:]` — oldest first, most recent **last** (as `drift_confirmed`'s docstring correctly says). Harmless under `sum()`, lethal the day someone adds recency weighting.

### 17. NIT — silent `[]` on bad `today`
`engine.py` ~L82 `except ValueError: return []` — a malformed pinned day silently produces a 100% fail-closed session in replay. Log it.

---

## AREAS I CHECKED AND FOUND CLEAN

- `drift_confirmed`'s fail-closed logic is correct: empty, partial (<3), non-finite, and bool-as-number inputs all return `False`; the loader independently filters the same cases. No fail-OPEN path in the gate's own logic.
- The horizon filter (`abs(dt_s - 120.0) > 1.0`) correctly prevents one moment's 30/60/120s triple from posing as three confirmations. Correct fix, correctly reasoned.
- `us_band_gated` revert to `(0.50, 0.80)` matches the registry's H-BAND1-AMEND2-REVERT exactly; the comparison is inclusive-both-ends, unchanged in semantics from before.
- `slate_instance`'s CORPUS-F1 coherence guard (`ct_score + t_score != round_number - 1 or seg_round != round_number`) mirrors the existing detector guard and correctly returns `None` on transition windows.
- `GRID_TEST_SERIES_IDS` exclusion in `handle_slate` is present and correct.
- The gate runs identically in dry_run — correct, and the right call for keeping the armed path byte-identical.

---

## WHAT I COULD NOT VERIFY FROM THIS BUNDLE (each is a finding, not an assumption)

1. `Recorder.handle()` body — whether it derives `a`/`b` from `instance["state"]` (finding 7) and whether it stamps `hypothesis_id` as `H-MAPENTRY1` (finding 8).
2. `build_us_intent()` body — whether it filters `src_hypothesis_id` (finding 6).
3. `_skip_row` / `_decision_row` payloads (finding 15) and the engine's exception handling around `_handle` (finding 10b).
4. The postquote sampler itself — orientation convention (3), units (2), file naming/timezone (11), and whether it samples all discounted candidates or only post-fill (which would make the gate a bootstrap deadlock).
5. Any real `series_state` row (finding 12).
6. Any real mapentry row with a **non-null** `market_slug` (finding 4).
7. **The single most important missing number:** the gate's measured admit rate on real capture. Nobody has replayed `recent_postquote_moves` over the 08-27/08-28 postquote files for every mapentry row that passed the band gate and printed `confirmed / total`. That one number would have caught the near-miss this morning and it would settle findings 1, 4, and 11 at once. Its absence — after a same-day 100%-skip near-miss — is itself the most serious process gap in the day's work. 555 green tests do not substitute for it.

---

## VERDICT

**FAIL** — do not arm. Findings 1–6 are blocking: the gate's threshold is one tick of resolution rather than the frozen +1.0pt bar and passes on the average case; the orientation, units, and slug-value seams that determine whether it does anything at all are unverified against real capture; the executor gate is cited as licensed by a registry row whose text does not license it and whose falsification criterion it contaminates; and the slate-coverage change routes an unregistered entry population into the live order path. The plumbing fixes from this morning's near-miss are real and correct — but the same defect class survived one layer down, in the *values* rather than the *names*.