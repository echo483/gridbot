# H-POSTSHOCKFADE1 anchor trace — reproducing 108/70/32 with a per-gate funnel

Author: gridbot-reasoner, 2026-09-01 (~23:30–23:55 PDT), in response to the
builder-lane report that two spec-literal implementations land ~170 DOWN /
~51 UP pre-persistence (~99 entries) and cannot hit the design brief's
anchors (108 no-persist → 70 persisted deduped DOWN, 55 series; 32 UP with
persistence / 48 without).

Method: I recovered the ORIGINAL verification session's transcript from my own
profile history (session 20260901_031304_d9fdf2, "Write post-shock fade
design brief"), extracted the exact code that produced the anchors (that
session's msg 286, executed 2026-09-01 10:34:41 UTC), reconstructed the
corpus as it existed on disk at that instant, and re-ran everything below.
Every number in this doc is from a run executed while writing it — none from
memory.

## VERDICT UP FRONT

**(a) REPRODUCED — exactly, all four anchors (108, 70, 48, 32), first
attempt.** But the reproduction exposes the root cause of the builders'
failure: **§1.2 of the design brief does not describe the pipeline that
produced the anchors.** The anchor run's population filter was
`direction + sequence_key + market_slug present` — and NOTHING ELSE. The two
population gates §1.1/§1.2 name (`prev_p_map ≥ 0.40` and `not
ended_by_restart`) were **never applied in the anchor run**; that is a bug in
my original verification, not a reading the builders should have guessed.
Additionally the corpus scope was never pinned in the brief, and the 09-01
day file has since completed (3,037 rows now vs 705 then) and a 09-02 file
now exists — on today's corpus even the session-exact pipeline gives 122/74
DOWN and 51/33 UP, not 108/70.

So: the anchors are reproducible, they are real, and they are also **not the
funnel §1.2 specifies**. Section 5 gives the verdict consequence and the
exact wording fix.

---

## 1. The corpus scope, pinned (the first thing the brief failed to do)

Anchor-run instant: 2026-09-01T10:34:41.536 UTC (msg-286 issue timestamp in
the transcript). At that instant the corpus was the seven day files
08-26→09-01, with mapentry-20260901.jsonl MID-WRITE (capture was live):
only its first 705 rows, max ts_ns 1788258303057068175
(2026-09-01T10:25:03Z), were on disk. mapentry-20260902.jsonl did not exist.

Verified now (wc -l, per-file max ts_ns; "at run time" for 09-01 established
by truncating today's monotonic file at the run instant and confirming the
footer counts reproduce at exactly N=705 — see §2 sweep):

| file | lines NOW | max ts_ns NOW | state at anchor-run time (10:34:41Z) |
|---|---|---|---|
| mapentry-20260826.jsonl | 4,674 | 1787788733668337301 (08-26T23:58:53Z) | identical |
| mapentry-20260827.jsonl | 4,851 | 1787875188761247253 (08-27T23:59:48Z) | identical |
| mapentry-20260828.jsonl | 3,387 | 1787961561480660681 (08-28T23:59:21Z) | identical |
| mapentry-20260829.jsonl | 3,190 | 1788043444511075305 (08-29T22:44:04Z) | identical |
| mapentry-20260830.jsonl | 6,866 | 1788134384607878219 (08-30T23:59:44Z) | identical |
| mapentry-20260831.jsonl | 4,798 | 1788220751288614979 (08-31T23:59:11Z) | identical |
| mapentry-20260901.jsonl | 3,037 | 1788307196476113060 (09-01T23:59:56Z) | **705 rows, max ts 1788258303057068175 (10:25:03Z)** |
| mapentry-20260902.jsonl | 324 | 1788325177855409581 (09-02T04:59:37Z) | did not exist |
| **corpus total** | 31,127 | | **28,471 rows** |

The footer counts of the brief (28,471 rows → 1,065 shocks / 554 priced /
409 DOWN-priced) reproduce EXACTLY on files 0–5 complete + first 705 rows of
09-01, and on no other truncation (swept N=680..744; unique match at N=705).
This pins the corpus scope unambiguously for any future reproduction:
**rows with ts_ns ≤ 1788258303057068175 on the 09-01 file; seven files.**

Both builders' sweeps ("corpus truncated to ≤08-30" among the variants)
could not hit this, because the anchor corpus is NOT a day boundary — it is a
mid-day snapshot of a live-writing file.

## 2. The exact pipeline (session msg-286, verbatim + per-gate counters)

This is the code that produced 108/70/48/32, re-run now with counters at
every stage. Gate semantics as EXECUTED (diffs vs §1.2 text flagged inline):

- Population: `direction == X AND sequence_key resolved AND market_slug
  present`. **No prev_p_map gate. No ended_by_restart exclusion.** (GATES
  THE SPEC NAMES BUT THE RUN SKIPPED — the core bug.)
- "Book alive / entry quote": FIRST live quote in [t0, t0+2s] where live =
  bid, ask, p_map all numeric, bid>0, ask>0, ask>bid — read from the RAW
  `bid`/`ask` fields (not `side_ask`). In practice offset 0 = the shock row
  itself, but a dead shock row can be rescued by a live row ≤2s later —
  §1.2 says "shock-row book alive", a stricter reading.
- Staleness gate: on that first live quote, DOWN: bid − p_map ≥ 0.10;
  UP: p_map − ask ≥ 0.10.
- Persistence (PERSIST arm): ANY later live quote in (off, off+45s] with
  gap ≥ 0.05 (same gap formula). §1.2 says "the NEXT same-sequence live
  quote" — any-vs-next is a second textual mismatch (rarely material:
  offsets are ~always 0 here).
- Dedup: one per (series_id, map_number, market_slug), entries sorted by
  quote offset then first wins.

```python
# SESSION-EXACT anchor pipeline (msg-286 verbatim + per-gate counters).
# Usage: CORPUS=anchor|today uv run python this_file.py
#   anchor = the corpus as it existed at 2026-09-01T10:34:41Z (the pin)
#   today  = whatever is on disk now
import sys, glob, os, bisect
sys.path.insert(0, 'scripts')
import postshock_lag as pl

MODE = os.environ.get("CORPUS", "anchor")
CUTOFF = 1788258303057068175  # 09-01 file's on-disk max ts_ns at run time

if MODE == "anchor":
    files = sorted(f for f in glob.glob('data/shadow/mapentry-2026*.jsonl')
                   if not f.endswith('20260902.jsonl'))
else:
    files = sorted(glob.glob('data/shadow/mapentry-*.jsonl'))
rows, _ = pl.load_data(files)
if MODE == "anchor":  # truncate the 09-01 file (tag index 6) to run-time state
    rows = [r for r in rows
            if not (r.get(pl.SOURCE_FILE_TAG) == 6
                    and isinstance(r.get('ts_ns'), int)
                    and r['ts_ns'] > CUTOFF)]
print(f"CORPUS {MODE}: {len(rows)} rows")
res = pl.detect(rows)          # defaults: shock .25, window 300s, converge .05
sh = res['shocks']
print(f"shocks={len(sh)} priced(n_book_obs>0)="
      f"{sum(1 for r in sh if r['n_book_obs']>0)}")

NS = 1_000_000_000
by_key = {}
for r in rows:
    key = (r.get(pl.SOURCE_FILE_TAG), pl._norm_key_part(r.get('series_id')),
           pl._norm_key_part(r.get('map_number')), pl._norm_key_part(r.get('team')))
    if isinstance(r.get('ts_ns'), int):
        by_key.setdefault(key, []).append(r)
for v in by_key.values():
    v.sort(key=lambda r: r['ts_ns'])
ts_lists = {k: [r['ts_ns'] for r in v] for k, v in by_key.items()}
keyindex = {}
for key, seq in by_key.items():
    for r in seq:
        keyindex.setdefault((key[1], key[2], key[3], r['ts_ns']), key)
for rec in sh:
    rec['_key'] = keyindex.get((pl._norm_key_part(rec['series_id']),
                                pl._norm_key_part(rec['map_number']),
                                pl._norm_key_part(rec['team']),
                                rec['shock_ts_ns']))

def num(x):
    try:
        f = float(x); return f if f == f else None
    except (TypeError, ValueError):
        return None

def quotes_in(rec, lo, hi):
    seq = by_key[rec['_key']]; tss = ts_lists[rec['_key']]; t0 = rec['shock_ts_ns']
    out = []; i = bisect.bisect_left(tss, t0 + lo * NS)
    while i < len(seq) and seq[i]['ts_ns'] <= t0 + hi * NS:
        r = seq[i]
        bid, ask, p = num(r.get('bid')), num(r.get('ask')), num(r.get('p_map'))
        if None not in (bid, ask, p) and bid > 0 and ask > 0 and ask > bid:
            out.append((bid, ask, p, (r['ts_ns'] - t0) / NS))
        i += 1
    return out

def run(direction, gap_field, gap_min, persist,
        persist_horizon=45, persist_gap=0.05):
    n_all = sum(1 for r in sh if r['direction'] == direction)
    n_key = sum(1 for r in sh if r['direction'] == direction and r.get('_key'))
    pop = [r for r in sh if r['direction'] == direction
           and r.get('_key') and r['market_slug']]
    ents = []; n_noquote = n_gapfail = n_persistfail = 0
    for rec in pop:
        qs = quotes_in(rec, 0, 2)            # entry quote: first live in [0,+2s]
        if not qs: n_noquote += 1; continue
        bid, ask, p, off = qs[0]
        gap = gap_field(bid, ask, p)
        if gap < gap_min: n_gapfail += 1; continue
        if persist:
            later = quotes_in(rec, off, off + persist_horizon)
            if not any(gap_field(b2, a2, p2) >= persist_gap
                       for b2, a2, p2, _ in later[1:]):
                n_persistfail += 1; continue
        ents.append({'sid': rec['series_id'], 'map': rec['map_number'],
                     'slug': rec['market_slug'], 'off': off})
    seen = {}
    for e in sorted(ents, key=lambda e: e['off']):
        seen.setdefault((e['sid'], e['map'], e['slug']), e)
    out = list(seen.values())
    print(f"  [{direction}] gates: all={n_all} -> key={n_key} "
          f"-> slug={len(pop)} -> livequote[0,2]s={len(pop)-n_noquote} "
          f"(drop {n_noquote}) -> gap>=0.10={len(pop)-n_noquote-n_gapfail} "
          f"(drop {n_gapfail}) -> raw={len(ents)} "
          f"(persist drop {n_persistfail}) -> deduped={len(out)} "
          f"series={len(set(e['sid'] for e in out))}")
    return out

down_gap = lambda b, a, p: b - p
up_gap   = lambda b, a, p: p - a
for label, persist in (('NO-PERSIST', False), ('PERSIST(45s,>=0.05)', True)):
    print(f"== {label} ==")
    run('down', down_gap, 0.10, persist)   # DOWN candidate arm
    run('up',   up_gap,   0.10, persist)   # UP control arm
```

Actual output, re-run this session:

```
CORPUS anchor: 28471 rows
shocks=1065 priced(n_book_obs>0)=554
== NO-PERSIST ==
  [down] gates: all=796 -> key=796 -> slug=415 -> livequote[0,2]s=390 (drop 25)
         -> gap>=0.10=200 (drop 190) -> raw=200 (persist drop 0)
         -> deduped=108 series=76
  [up]   gates: all=270 -> key=270 -> slug=149 -> livequote[0,2]s=144 (drop 5)
         -> gap>=0.10=53 (drop 91) -> raw=53 (persist drop 0)
         -> deduped=48 series=43
== PERSIST(45s,>=0.05) ==
  [down] ... -> raw=88 (persist drop 112) -> deduped=70 series=55
  [up]   ... -> raw=32 (persist drop 21)  -> deduped=32 series=30
```

**108 → 70 DOWN (55 series), 48 → 32 UP: all four brief anchors, exact.**
(Original session output for cross-check: "DOWN candidate: deduped 108 (of
415 priced down shocks); series 76 ... PERSIST: deduped 70 ... series 55 ...
UP control: deduped 48 / 32" — identical.)

Same code on TODAY's corpus (8 files, 31,127 rows): NO-PERSIST DOWN 122 (85
series) / UP 51 (46); PERSIST DOWN 74 (59) / UP 33 (31). **This is where the
builders' corpus-truncation sweep was doomed**: the anchor corpus is a
mid-day live-file snapshot, not a day boundary, and the corpus has grown
since.

## 3. The spec-literal funnel, per-gate, for contrast

What §1.2 ACTUALLY says, implemented literally (population gates included;
staleness/persistence measured on the shock ROW itself; persistence = the
NEXT live quote only):

```python
# SPEC-LITERAL funnel (§1.2 as written). Same corpus pinning as above
# (identical load/truncate/detect/by_key preamble — omitted here for
# brevity; identical code to the block above through `def num`).
def shock_row(rec):
    seq = by_key[rec['_key']]; tss = ts_lists[rec['_key']]
    i = bisect.bisect_left(tss, rec['shock_ts_ns'])
    return seq[i] if i < len(seq) and seq[i]['ts_ns'] == rec['shock_ts_ns'] else None

def funnel(direction, gapfn):
    shocks  = [r for r in sh if r['direction'] == direction and r.get('_key')]
    priced  = [r for r in shocks if r['n_book_obs'] > 0]
    prev_ok = [r for r in priced if (r['prev_p_map'] or -1) >= 0.40]   # §1.1 DEV 1
    no_res  = [r for r in prev_ok if not r['ended_by_restart']]       # §1.1 rule
    slugged = [r for r in no_res if r['market_slug']]
    alive = []
    for r in slugged:                                                  # §1.2 cl.1
        sr = shock_row(r)
        if sr is None: continue
        bid, ask, p = num(sr.get('bid')), num(sr.get('ask')), num(sr.get('p_map'))
        if None not in (bid, ask, p) and bid > 0 and ask > 0 and ask > bid:
            alive.append((r, bid, ask, p))
    gated = [t for t in alive if gapfn(t[1], t[2], t[3]) >= 0.10]      # §1.2 cl.2
    def persist_ok(rec):                                               # §1.2 cl.3
        seq = by_key[rec['_key']]; tss = ts_lists[rec['_key']]; t0 = rec['shock_ts_ns']
        i = bisect.bisect_left(tss, t0)
        for j in range(i, len(seq)):
            rr = seq[j]
            if rr['ts_ns'] <= t0: continue
            if rr['ts_ns'] > t0 + 45 * NS: break
            b2, a2, p2 = num(rr.get('bid')), num(rr.get('ask')), num(rr.get('p_map'))
            if None in (b2, a2, p2) or b2 <= 0 or a2 <= 0 or a2 <= b2: continue
            return gapfn(b2, a2, p2) >= 0.05        # NEXT live quote only
        return False
    ents = [t for t in gated if persist_ok(t[0])]
    seen = {}
    for t in sorted(ents, key=lambda t: t[0]['shock_ts_ns']):
        r = t[0]; seen.setdefault((r['series_id'], r['map_number'], r['market_slug']), t)
    print(direction, 'shocks', len(shocks), 'priced', len(priced),
          'prev>=0.40', len(prev_ok), 'no-restart', len(no_res),
          'slug', len(slugged), 'alive', len(alive), 'gate', len(gated),
          'persist-raw', len(ents), 'deduped', len(seen),
          'series', len(set(t[0]['series_id'] for t in seen.values())))
funnel('down', lambda b, a, p: b - p)
funnel('up',   lambda b, a, p: p - a)
```

Actual output, both corpora, re-run this session:

```
CORPUS anchor (28,471 rows):
down shocks 796 priced 410 prev>=0.40 389 no-restart 334 slug 332
     alive 315 gate 162 persist-raw 83 deduped 67 series 53
up   shocks 270 priced 145 prev>=0.40 79  no-restart 72  slug 71
     alive 71  gate 42  persist-raw 37 deduped 37 series 34

CORPUS today (31,127 rows):
down shocks 863 priced 458 prev>=0.40 435 no-restart 374 slug 372
     alive 354 gate 180 persist-raw 89 deduped 72 series 58
up   shocks 293 priced 162 prev>=0.40 86  no-restart 78  slug 77
     alive 77  gate 43  persist-raw 38 deduped 38 series 35
```

The builders' ~170 DOWN / ~51 UP pre-persistence sits right in this
funnel's territory (my literal run: 162/42 on the anchor corpus, 180/43 on
today's; exact value depends on their quote-row and restart variants).
**They implemented the spec; the spec was not the pipeline.** Their 11-variant
sweep could not land on 108/32 because no variant of §1.2's TEXT generates
those numbers — the missing dimension was "the verification run skipped two
of its own gates," which no reading of the text suggests.

## 4. The exact clause-by-clause delta (what the builders misread — because it was never written)

| # | §1.1/§1.2 text | Session run that made the anchors | Effect |
|---|---|---|---|
| 1 | cl.1 "prev_p_map ≥ 0.40" | NOT APPLIED (pool = all DOWN-with-slug, n=415) | anchor pool 415, not 389 |
| 2 | cl.1 "not ended_by_restart" | NOT APPLIED | anchor pool keeps the 55 restart-flagged DOWN shocks |
| 3 | cl.1 "shock-row book alive" | first LIVE quote in [t0, t0+2s] (raw bid/ask fields) | rescues shocks whose own row is dead-book (+75-ish admissions vs shock-row-only on the anchor corpus) |
| 4 | cl.2 "shock-row gap bid − p_map ≥ 0.10" | gap measured on that first live quote (≈shock row when offset 0) | ≈equivalent here (offsets ~always 0) |
| 5 | cl.3 "the NEXT same-sequence live quote within 45s" | ANY later live quote in (off, off+45s] | minor; offsets ~0 so windows coincide |
| 6 | dedup "earliest qualifying shock wins" | entries sorted by quote offset, first wins | equivalent at offset 0 |
| 7 | corpus "full corpus 08-26→09-01" | 7 files with 09-01 TRUNCATED at its 10:34Z write head (705 rows); 09-02 absent | today's completed corpus moves even the session-exact pipeline to 122/74 |
| 8 | footer "409 DOWN-priced" vs anchor label "of 415" | 409 = n_book_obs>0; 415 = market_slug-present — two different "priced" definitions in the same brief | labeling confusion, no numeric effect |

Note: the brief's "415 → 394 priced DOWN shocks (verified run)" line for
DEVIATION 1 IS accurate (the session's exploratory msg-284 run printed
exactly that) — but that filtered pool was then NOT used for the anchor
counts; the msg-286 anchor run re-derived its pool from scratch without the
filter. That is the whole bug in one sentence.

## 5. Verdict and disposition

Verdict: **(a) REPRODUCED** — the anchors are real outputs of a real run,
reproduced exactly on the reconstructed corpus. But the anchors were
generated by a pipeline that is LOOSER than §1.2 on two population gates it
names, so:

**The bug is in the original verification, not in the spec's gates.** The
fallen-favorite filter (prev_p_map ≥ 0.40) and the ended_by_restart
exclusion both have explicit design rationales in §1.1 (a shock from 0.15→
0.05 is a decided map, not a falling favorite; a re-shock inside 300s means
the state is still moving). They were intended. The verification run simply
forgot to apply them when it re-derived its pool. The builders' spec-literal
funnel is the CORRECT reading of §1.2.

Consequences, stated plainly:

1. **The anchors 108/70/55-series and 48/32 are VOID as design-mode supply
   estimates for §1.2.** They describe the session's looser pool. Do not
   build or bar-calibrate against them.
2. **The reference funnel is the spec-literal one.** On the pinned anchor
   corpus: 162 gate-passing → 83 persisted → **67 deduped candidate entries
   / 53 series**; UP control 42 → 37 → **37 / 34 series**. On today's full
   corpus: 180 → 89 → **72 / 58 series**; UP 43 → 38 → **38 / 35 series**.
   (~10/day accrual still holds, so the 40/15 n-floor reasoning survives
   with room; state it against the spec-literal numbers.)
3. The interesting near-coincidence, honestly flagged: 70 (session,
   unfiltered pool, anchor corpus) vs 67 (spec-literal, anchor corpus) vs 72
   (spec-literal, today). The supply estimate was accidentally robust — but
   that is luck, not license; the pool definitions still must match.

Proposed wording fix for the design brief (§1.2 clause 1 and the bar-clause
note (i)) — exact replacement text:

> Candidate entry condition clause 1 (corrected): "DOWN shock, prev_p_map ≥
> 0.40, not ended_by_restart, market_slug present, shock-row book alive (bid
> and ask numeric, > 0, ask > bid on the shock row ITSELF — row t's fields,
> not a later quote)."

> Bar-clause note (i) (corrected): "design mode produced **67 deduped
> candidate entries / 53 series** over the pinned design corpus (seven day
> files 08-26→09-01, 09-01 truncated to its state at 2026-09-01T10:25:03Z —
> rows with ts_ns ≤ 1788258303057068175; 28,471 rows total; spec-literal
> gates), and 37/34 UP control entries. The earlier 108/70 anchor was
> computed on a pool MISSING the prev_p_map ≥ 0.40 and ended_by_restart
> gates (verification bug, docs/analysis/2026-09-01-postshock-anchor-trace.md)
> and is voided."

And clause 3, to kill the any-vs-next ambiguity: "persistence = the NEXT
same-sequence live quote (strictly after the shock row, within 45s of shock
ts_ns) shows bid − p_map ≥ 0.05; 'live' = bid and ask numeric, >0, ask>bid.
Any-vs-next was measured to be immaterial at this cadence, but the text pins
NEXT."

One open item I did not resolve (flagged, not papered over): §1.3 mirrors
the UP arm "same plumbing mirrored" without restating population gates. My
spec-literal run mirrored prev_p_map ≥ 0.40 onto UP shocks (79 of 145 UP
priced pass — for an UP shock, prev_p_map ≥ 0.40 means the riser started
above 0.40, a sensible "riser was a favorite" read, but it is MY reading,
not text). Without it, UP gate-pass is larger. The brief needs one sentence
pinning the UP population explicitly; I recommend mirroring the gate as run
here and stating so.

Bad-news summary for the record: my own verification run shipped anchors
from a pool that didn't apply two of the spec's own gates, and the brief
never pinned its corpus state. Both are exactly the class of error this
project's evidence-integrity discipline exists to catch — caught here by two
independent spec-literal builders who refused to fudge toward my numbers.
The spec-literal funnel is now the reference; the instrument build should
proceed against 67/37 (anchor corpus) or 72/38 (current corpus), not 108/70.

— gridbot-reasoner, 2026-09-01. All runs executed this session (read-only;
no writes beyond this file, no src/, no scripts/, no commits). Transcript
evidence: session 20260901_031304_d9fdf2, msgs 284–287.
