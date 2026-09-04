#!/usr/bin/env python3
"""roundwin-v2 Pool B (drift head) — drift-first accumulation/evaluation.

ANALYSIS LANE ONLY (design: docs/superpowers/specs/2026-08-28-roundwin-v2-design.md,
Pool B definition §2.2, anti-leakage protocol §2.3). Describe-only: no registry
edits, no src/ changes, no arming logic, no orders.

What it does
------------
1. Loads mapentry-*.jsonl (candidate entries: p_map, top-of-book, entry rule
   inputs) and postquote-*.jsonl (ORIENTED drift samples per book; each row IS a
   quote at eval time, `oriented_move` at dt_s ∈ {30,60,120} is the outcome).
2. Builds Pool B: qualifying mapentry rows joined to the drift evidence of
   their own book. Join key mapentry↔postquote is (slug, us_side) — mapentry
   carries `market_slug`, postquote carries `slug` (naming trap). Recording-
   only slate rows (engine slate gate, src_hypothesis_id ==
   SLATE_SRC_HYPOTHESIS_ID) are EXCLUDED — the engine never trades them.
   DEDUP ORDER is engine-faithful: the drift gate is evaluated over the FULL
   candidate population; the dedup slot is consumed ONLY on gate-pass
   placement (engine.py consumes self.seen on the placement path alone, so
   drift-fail retries keep the key live and a later confirmed crossing may
   enter).
3. Drift-first entry rule: the engine's own gate, imported from
   gridbot.executor.intents (DRIFT_WINDOW samples, mean ≥ DRIFT_MIN_CONFIRM,
   fail-closed). Evidence = oriented_move at the 120s horizon of the most
   recent DRIFT_WINDOW postquote samples of the same book STRICTLY EARLIER
   than the eval moment (src_ts_ns < t; never equal — §2.3.1).
4. F1 leak filter (inherited, binding on round-level feature rows): drop rows
   where rounds_played + 1 != round_number — BOTH legs of the assertion must
   agree (a row whose round_number leg is missing/non-numeric fails even when
   its score-derived rounds_played looks coherent). On mapentry rows
   rounds_played is the scores sum (a + b, or features.rounds_played when
   carried). Rows where the assertion cannot be evaluated are dropped too
   (fail closed, §2.3.4).
5. Scores fills-equivalent entries against resolved map winners (first-
   observation oracle: scripts/grid_winners.process_lines over the GRID
   series-state capture, or a prebuilt --winners document). Win = our team won
   the map; edge = won - entry_price (frozen entry rule, mirrored from
   scripts.entry_quality).
6. Emits describe-only metrics — entries, fills-equivalent, win rate, mean
   edge, series-clustered bootstrap CI — to stdout and a markdown report.

Run: uv run python scripts/roundwin2_poolb.py [--days 20260827,20260828] ...
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import math
import os
import random
import sys
from collections import defaultdict

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Canonical docs/ report — written ONLY when the operator passes --write-doc
# (default runs write to data/analysis/, gitignored). Env-overridable so the
# test suite can pin it to tmp_path instead of the real repo tree.
DOCS_REPORT_PATH = os.path.join(_PROJECT_ROOT, "docs", "analysis",
                                "2026-08-31-roundwin2-poolb.md")

from gridbot.executor.intents import (
    DRIFT_MIN_CONFIRM,
    DRIFT_WINDOW,
    SLATE_SRC_HYPOTHESIS_ID,
    US_BAND_GATE,
    drift_confirmed,
    us_band_gated,
)
from scripts.entry_quality import entry_price, load_winners

NS = 1_000_000_000
DRIFT_HORIZON_S = 120.0          # engine feeds 120s-oriented moves into the gate
BOOTSTRAP_B = 2000
BOOTSTRAP_SEED = 42

__all__ = [
    "apply_drift_gate",
    "book_history",
    "evidence_histogram",
    "f1_leak_ok",
    "main",
    "place_with_dedup",
    "prior_drift_moves",
    "reconcile",
    "reconcile_rows",
    "select_poolb_entries",
    "series_cluster_bootstrap",
    "settle_entries",
]


# ── loaders ──────────────────────────────────────────────────────────────────

def load_jsonl(paths: list[str]) -> tuple[list[dict], int]:
    """Load JSONL rows; unparseable lines are skipped and counted."""
    rows: list[dict] = []
    bad = 0
    for path in paths:
        try:
            fh = open(path, encoding="utf-8")  # noqa: SIM115 — opened outside `with` so FileNotFoundError is the only skip; closed by the with-block below
        except FileNotFoundError:
            continue
        with fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    bad += 1
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
    return rows, bad


def day_files(kind: str, days: list[str] | None) -> list[str]:
    """data/shadow/<kind>-<day>.jsonl paths, time-ordered (append order).
    A day value containing os.sep is used as an explicit file path and is
    only applied to the kind its basename starts with (<kind>-...)."""
    if days:
        paths = []
        for d in days:
            if os.sep in d or "/" in d:
                if os.path.basename(d).startswith(f"{kind}-"):
                    paths.append(d)
            else:
                paths.append(os.path.join(
                    _PROJECT_ROOT, "data", "shadow", f"{kind}-{d}.jsonl"))
        return [p for p in paths if os.path.exists(p)]
    return sorted(glob.glob(os.path.join(_PROJECT_ROOT, "data", "shadow",
                                         f"{kind}-*.jsonl")))


def day_of(row: dict) -> str:
    """UTC capture day of a row, from its capture timestamp (for strata)."""
    src = row.get("src_ts_ns")
    try:
        ts = float(src) / NS
        if not math.isfinite(ts):
            return "unknown"
        import datetime
        return datetime.datetime.fromtimestamp(
            ts, datetime.UTC).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError):
        return "unknown"


def _finite_num(v) -> float | None:
    """float(v) when v is a finite real number, else None (never raises)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


# ── F1 leak filter (inherited, binding on round-level feature rows) ─────────

def f1_leak_ok(row: dict) -> bool:
    """CORPUS-F1 discipline on a round-level row: rounds_played + 1 must equal
    round_number — BOTH legs of the assertion must agree (the features
    .round_number leg is the row's own round_number field; a row whose
    score-derived rounds_played is coherent but whose round_number field is
    missing/non-numeric cannot pass the assertion).  rounds_played is
    features.rounds_played when the row carries features, else the mapentry
    scores sum (a + b).  Rows where the assertion cannot be evaluated are NOT
    ok (fail closed, §2.3.4) — the repo was burned once by 13.0% leaked rows;
    unevaluable rows stay out."""
    feats = row.get("features")
    feats = feats if isinstance(feats, dict) else None
    # Corpus fact (review C, 2026-08-31): 0/11,664 mapentry rows carry a
    # features dict today, so the features leg below is currently dead code —
    # kept because mapentry may grow features (the F1 rule binds both legs
    # when present) and because dropping it would silently weaken the filter.
    if feats is not None and feats.get("rounds_played") is not None:
        # features.rounds_played leg: validated exactly like the round_number
        # leg (a bare float() here once accepted bools and could raise on
        # strings — both legs of the assertion must be evaluable numbers).
        rp = _finite_num(feats["rounds_played"])
        if rp is None:
            return False
    else:
        a, b = _finite_num(row.get("a")), _finite_num(row.get("b"))
        if a is None or b is None:
            return False
        rp = a + b
    rn = _finite_num(row.get("round_number"))
    if rn is None:
        return False
    return rp + 1.0 == rn


# ── book history / drift evidence (engine-faithful, §2.3 as-of protocol) ────

def book_history(postquote_rows: list[dict], horizon: float | None = DRIFT_HORIZON_S) -> dict:
    """(slug, us_side) -> time-sorted [(ts_ns, oriented_move), ...].

    Mirrors the engine's recent_postquote_moves exactly:
    - keyed by (slug, us_side) ONLY — same-book isolation (§2.3.3): a LONG
      book's moves never inform its SHORT counterpart;
    - default: only the 120s horizon, with the engine's ±1s tolerance (three
      views of one moment can never masquerade as three confirmations);
      horizon=None pools ALL horizons (reconciliation relaxant ONLY — the
      live gate reads 120s only);
    - ordered by ts_ns, the row's WRITE time — a 120s sample materializes
      ~120s after its eval, so it cannot be evidence for that eval's own
      decision (§2.3.2: rows written after t are invisible even when
      physically present)."""
    hist: dict[tuple, list[tuple[int, float]]] = defaultdict(list)
    for r in postquote_rows:
        if r.get("type") != "postquote":
            continue
        try:
            dt = float(r.get("dt_s"))
        except (TypeError, ValueError, OverflowError):
            continue
        if horizon is not None and abs(dt - horizon) > 1.0:
            continue
        move = _finite_num(r.get("oriented_move"))
        if move is None:
            continue    # non-finite / non-numeric / overflow — never evidence
        key = (str(r.get("slug") or r.get("market_slug")), r.get("us_side"))
        try:
            hist[key].append((int(r.get("ts_ns")), move))
        except (TypeError, ValueError, OverflowError):
            continue
    for v in hist.values():
        v.sort(key=lambda x: x[0])   # chronological, so [-n:] is most recent
    return dict(hist)


DRIFT_LOOKBACK_NS = 2 * 86400 * NS   # engine reads today + yesterday files


def prior_drift_moves(hist: dict, slug, us_side, t_ns: int,
                      window: int = DRIFT_WINDOW) -> list[float]:
    """The engine's drift evidence for a book deciding at write-time t_ns:
    oriented 120s moves of the same book whose rows were WRITTEN strictly
    before t (never equal — §2.3.1/§2.3.2), within the engine's 2-day
    (today+yesterday) file window, oldest first, last `window` samples."""
    rows = hist.get((str(slug), us_side)) or []
    floor = int(t_ns) - DRIFT_LOOKBACK_NS
    return [m for ts, m in rows if floor <= ts < int(t_ns)][-window:]


# ── Pool B candidate selection (engine record-only gates; NO dedup here) ────

def select_poolb_entries(mapentry_rows: list[dict]) -> tuple[list[dict], dict]:
    """Gates qualifying mapentry rows into the Pool B CANDIDATE population.

    Order mirrors the engine's record-only gates (engine.py _process: seen
    check -> slate gate -> band gate -> drift gate), but this function does
    NOT dedup: the engine consumes its dedup slot ONLY when a row finally
    PLACES (engine.py: self.seen.add runs on the placement path alone; every
    skip returns before it).  Deduping here — the old script's bug — pinned
    each book to its FIRST qualifying row and made the drift gate
    unsatisfiable: a book's first crossing has no visible postquote history
    yet, and the later crossings that could confirm were already deduped out.

    Gate order (record-only, all non-consuming, engine-faithful):
    malformed -> qualify gate -> book validity (slug/side/bid) -> entry price
    in range -> slate-row exclusion (engine slate gate: recording-only
    queue-16 rows, src_hypothesis_id == SLATE_SRC_HYPOTHESIS_ID, never
    traded, never consume their key) -> F1 leak filter (leaky rows stay out
    BEFORE any dedup accounting, so a leaky first row cannot block the later
    coherent row for the same book — engine-faithful: nothing skipped pre-
    placement ever consumes the key).

    Returns (candidates, counters).  Dedup happens AFTER the drift gate, in
    place_with_dedup."""
    counters = {
        "rows_loaded": 0, "malformed_row": 0, "not_qualifying": 0,
        "no_book": 0, "entry_price_out_of_range": 0, "slate_row": 0,
        "f1_leak_filtered": 0, "candidates": 0,
    }
    entries: list[dict] = []
    for row in mapentry_rows:
        counters["rows_loaded"] += 1
        if row.get("type") != "mapentry":
            counters["malformed_row"] += 1
            continue
        if row.get("qualifies") is not True:
            counters["not_qualifying"] += 1
            continue
        slug = row.get("market_slug")
        us_side = row.get("us_side")
        side_ask = row.get("side_ask")
        entry_max = row.get("entry_price_max")
        if _finite_num(side_ask) is None or _finite_num(entry_max) is None:
            counters["malformed_row"] += 1
            continue
        if not slug or us_side not in ("long", "short") or row.get("bid") is None:
            counters["no_book"] += 1
            continue
        price = entry_price(side_ask, entry_max)
        if price is None:
            counters["entry_price_out_of_range"] += 1
            continue
        if str(row.get("src_hypothesis_id") or "") == SLATE_SRC_HYPOTHESIS_ID:
            counters["slate_row"] += 1
            continue
        if not f1_leak_ok(row):
            counters["f1_leak_filtered"] += 1
            continue
        out = dict(row)
        out["entry_price"] = price
        out["slug"] = str(slug)            # normalized join name (postquote's)
        entries.append(out)
    counters["candidates"] = len(entries)
    return entries, counters


# ── drift gate application + settlement ─────────────────────────────────────

def apply_drift_gate(entries: list[dict], hist: dict) -> list[dict]:
    """Attach drift evidence and the engine's drift-first verdict to each
    candidate.  Fail-closed is delegated to gridbot.executor.intents
    .drift_confirmed (missing/partial/non-finite ⇒ no entry)."""
    for e in entries:
        try:
            t_ns = int(e.get("ts_ns"))   # decision time = record write time
        except (TypeError, ValueError, OverflowError):
            t_ns = None
        slug = e.get("slug") or e.get("market_slug")   # naming trap fallback
        moves = ([] if t_ns is None else prior_drift_moves(
            hist, slug, e.get("us_side"), t_ns))
        e["drift_moves"] = moves
        e["drift_confirmed"] = bool(drift_confirmed(moves))
    return entries


def place_with_dedup(candidates: list[dict]) -> tuple[list[dict], dict]:
    """Engine-faithful placement pass: the dedup slot is consumed ONLY when a
    candidate finally PLACES — exactly the engine's own accounting (engine.py:
    self.seen.add runs on the placement path alone; every skip — slate, band,
    drift-fail, second-side, capacity — returns before it, so a drift-fail
    retry keeps the key live).

    Gate order mirrors engine._process after intent build: seen check (the
    caller passes only rows from the non-consuming record gates) -> band gate
    (record-only, US_BAND_GATE) -> drift gate (the candidate's precomputed
    drift_confirmed).  The FIRST gate-passing crossing of a book claims its
    slot; later confirmed crossings of the same book are counted
    duplicate_key_after_gate (they never average in — the slot is consumed).

    Returns (placed_entries, counters)."""
    counters = {"band_gated": 0, "no_drift_confirm": 0,
                "duplicate_key_after_gate": 0, "placed": 0}
    seen: set[tuple] = set()
    placed: list[dict] = []
    for e in candidates:
        if us_band_gated(e.get("p_map")):
            counters["band_gated"] += 1
            continue
        if not e.get("drift_confirmed"):
            counters["no_drift_confirm"] += 1
            continue
        key = (str(e.get("series_id")), e.get("map_number"),
               str(e.get("slug")), e.get("us_side"))
        if key in seen:
            counters["duplicate_key_after_gate"] += 1
            continue
        seen.add(key)          # the ONLY place the slot is consumed
        counters["placed"] += 1
        placed.append(e)
    return placed, counters


def evidence_histogram(entries: list[dict]) -> dict:
    """Distribution of visible same-book sample counts (capped at the engine
    window) over the would-trade population — diagnoses WHY the gate
    confirms or fail-closes (a describe-only instrument must say which)."""
    counts: dict[str, int] = {"0": 0, "1": 0, "2": 0, "3+": 0}
    for e in entries:
        n = len(e.get("drift_moves") or [])
        counts["3+" if n >= DRIFT_WINDOW else str(n)] += 1
    return counts


def settle_entries(entries: list[dict], winners: dict[str, dict]) -> tuple[
        list[dict], dict]:
    """Score entries against the first-observation map-winner oracle.

    Winners are never guessed: an entry without a usable verdict for its
    (series, map) is excluded and counted.  won ∈ {1.0, 0.0} by exact team
    name; edge = won - entry_price."""
    excluded = {"no_grid_verdict": 0, "team_not_in_verdict": 0}
    settled: list[dict] = []
    for e in entries:
        key = f"{e.get('series_id')}:{e.get('map_number')}"
        v = winners.get(key)
        if not isinstance(v, dict) or not v.get("winner"):
            excluded["no_grid_verdict"] += 1
            continue
        w = v["winner"]
        if e.get("team") == w:
            won = 1.0
        elif e.get("opponent") == w:
            won = 0.0
        else:
            excluded["team_not_in_verdict"] += 1
            continue
        out = dict(e)
        out["won"] = won
        out["edge"] = won - e["entry_price"]
        settled.append(out)
    return settled, excluded


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def _metrics(settled: list[dict]) -> dict:
    n = len(settled)
    if n == 0:
        return {"n": 0, "win_rate": None, "mean_edge": None,
                "total_edge": None}
    wins = [s["won"] for s in settled]
    edges = [s["edge"] for s in settled]
    return {
        "n": n,
        "win_rate": _mean(wins),
        "mean_edge": _mean(edges),
        "mean_edge_pts": 100.0 * _mean(edges),
        "total_edge": sum(edges),
        "series": len({s.get("series_id") for s in settled}),
        "days": sorted({day_of(s) for s in settled}),
    }


def series_cluster_bootstrap(settled: list[dict], b: int = BOOTSTRAP_B,
                            seed: int = BOOTSTRAP_SEED) -> dict:
    """Series-clustered bootstrap CI (clusters = series_id, the §4.1 unit).
    Resample clusters with replacement to the original cluster count, pool
    their entries, and take percentile intervals.  Deterministic (seeded)."""
    clusters: dict[str, list[dict]] = defaultdict(list)
    for s in settled:
        clusters[str(s.get("series_id"))].append(s)
    keys = sorted(clusters)
    if not keys or b <= 0:
        return {"b": b, "seed": seed, "clusters": len(keys),
                "win_rate_ci": None, "mean_edge_ci": None}
    rng = random.Random(seed)
    wr, ed = [], []
    for _ in range(b):
        pool: list[dict] = []
        for k in (keys[rng.randrange(len(keys))] for _ in range(len(keys))):
            pool.extend(clusters[k])
        if not pool:
            continue
        wr.append(_mean([s["won"] for s in pool]))
        ed.append(_mean([s["edge"] for s in pool]))
    wr_s, ed_s = sorted(wr), sorted(ed)
    lo, hi = int(0.025 * len(wr_s)), int(0.975 * len(wr_s))
    return {
        "b": b, "seed": seed, "clusters": len(keys),
        "win_rate_ci": [wr_s[lo], wr_s[max(lo, hi - 1)]],
        "mean_edge_ci": [ed_s[lo], ed_s[max(lo, hi - 1)]],
    }


# ── reconciliation: replay live ledger decisions vs the capture corpus ─────

# Live-row variants, each relaxing EXACTLY ONE leg of the engine's evidence
# path (recent_postquote_moves: same slug+side, 120s horizon, last 3,
# today+yesterday file window; drift_confirmed: mean >= +1.0pt over the full
# visible window).  "exact" relaxes nothing.  These exist to say whether the
# live bar is strict or effectively unreachable — they do NOT license any
# trade (describe-only).
RECONCILE_VARIANTS = {
    "exact":            {"window": DRIFT_WINDOW, "horizon": DRIFT_HORIZON_S,
                         "lookback_ns": DRIFT_LOOKBACK_NS},
    "no_window_cap":    {"window": 10 ** 9, "horizon": DRIFT_HORIZON_S,
                         "lookback_ns": DRIFT_LOOKBACK_NS},
    "no_2day_window":   {"window": DRIFT_WINDOW, "horizon": DRIFT_HORIZON_S,
                         "lookback_ns": 10 ** 18},
    "all_horizons":     {"window": DRIFT_WINDOW, "horizon": None,
                         "lookback_ns": DRIFT_LOOKBACK_NS},
    "bar_0.5pt":        {"window": DRIFT_WINDOW, "horizon": DRIFT_HORIZON_S,
                         "lookback_ns": DRIFT_LOOKBACK_NS,
                         "min_confirm": 0.005},
    "n_ge_1":           {"window": DRIFT_WINDOW, "horizon": DRIFT_HORIZON_S,
                         "lookback_ns": DRIFT_LOOKBACK_NS, "n_ge_1": True},
}


def load_intents_rows(paths: list[str]) -> tuple[list[dict], int]:
    """Engine ledger rows (data/executor/intents-*.jsonl); malformed skipped
    and counted.  Same shape tolerance as load_jsonl but never silently joins
    two files' days — paths are explicit."""
    rows, bad = [], 0
    for path in paths:
        try:
            fh = open(path, encoding="utf-8")  # noqa: SIM115 — opened outside `with` so FileNotFoundError is the only skip; closed by the with-block below
        except FileNotFoundError:
            continue
        with fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    bad += 1
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
    return rows, bad


def _variant_confirmed(hist: dict, slug: str, us_side, t_ns, spec: dict) -> bool:
    """The engine's drift verdict for ONE live decision under a variant spec
    (same write-time evidence rule, relaxed per the variant table)."""
    try:
        t = int(t_ns)
    except (TypeError, ValueError, OverflowError):
        return False
    rows = hist.get((str(slug), us_side)) or []
    floor = t - int(spec["lookback_ns"])
    moves = [m for ts, m in rows if floor <= ts < t][-(int(spec["window"])):]
    if not moves:
        return False
    if spec.get("n_ge_1"):
        return sum(moves) / len(moves) >= float(spec.get("min_confirm", DRIFT_MIN_CONFIRM))
    return drift_confirmed(moves,
                           min_confirm=float(spec.get("min_confirm", DRIFT_MIN_CONFIRM)))


def reconcile_rows(intents_rows: list[dict], pq_rows: list[dict],
                   statuses: tuple = ("skipped:no_drift_confirm",)) -> dict:
    """Replay live ledger decisions under the exact live evidence path and
    each relaxed variant.

    The replay uses the SAME lookup the engine ran: same (slug, us_side) key,
    row WRITE times (ts_ns), strictly-before visibility, today+yesterday file
    window, last DRIFT_WINDOW samples, mean >= DRIFT_MIN_CONFIRM.  One known
    asymmetry, stated in the report: the engine tails only the LAST 1MB of
    each day-file, the replay reads the whole file — so the replay can see a
    sample the engine's tail had already slid past (relaxes the replay, if
    anything).

    Returns a describe-only doc: per-variant confirmed counts + agreement."""
    hists = {name: book_history(pq_rows, horizon=spec["horizon"])
             for name, spec in RECONCILE_VARIANTS.items()}
    doc: dict = {"replayed_rows": 0, "variants": {}}
    for name in RECONCILE_VARIANTS:
        doc["variants"][name] = {
            "confirmed": 0, "n": 0,
            "moves_empty": 0, "moves_lt_window": 0, "moves_ge_window": 0,
            "mean_move_pts": None,
        }
    sums = {name: 0.0 for name in RECONCILE_VARIANTS}
    for r in intents_rows:
        if r.get("status") not in statuses:
            continue
        doc["replayed_rows"] += 1
        slug = r.get("market_slug") or r.get("slug")
        t_ns = r.get("ts_ns")
        for name, spec in RECONCILE_VARIANTS.items():
            v = doc["variants"][name]
            v["n"] += 1
            hist = hists[name]
            try:
                t = int(t_ns)
            except (TypeError, ValueError, OverflowError):
                t = None
            rows = (hist.get((str(slug), r.get("us_side"))) or []) if t is not None else []
            floor = (t - int(spec["lookback_ns"])) if t is not None else 0
            moves = ([m for ts, m in rows if floor <= ts < t]
                     [-(int(spec["window"])):]) if t is not None else []
            if not moves:
                v["moves_empty"] += 1
            elif len(moves) < DRIFT_WINDOW:
                v["moves_lt_window"] += 1
            else:
                v["moves_ge_window"] += 1
            if moves:
                sums[name] += sum(moves) / len(moves)
            if _variant_confirmed(hist, slug, r.get("us_side"), t_ns, spec):
                v["confirmed"] += 1
    for name in RECONCILE_VARIANTS:
        v = doc["variants"][name]
        nonempty = v["n"] - v["moves_empty"]
        v["mean_move_pts"] = (100.0 * sums[name] / nonempty) if nonempty else None
    return doc


def reconcile(intents_days: list[str] | None = None) -> dict:
    """End-to-end reconciliation over the real corpus: live no_drift_confirm
    decisions from data/executor/intents-*.jsonl replayed against the
    data/shadow/postquote corpus, exact rule + relaxed variants."""
    root_exec = os.path.join(_PROJECT_ROOT, "data", "executor")
    if intents_days:
        paths = [os.path.join(root_exec, f"intents-{d}.jsonl")
                 for d in intents_days]
    else:
        paths = sorted(glob.glob(os.path.join(root_exec, "intents-*.jsonl")))
    rows, bad = load_intents_rows(paths)
    pq_rows, _ = load_jsonl(day_files("postquote", None))
    doc = reconcile_rows(rows, pq_rows)
    doc["intents_files"] = [os.path.basename(p) for p in paths]
    doc["intents_rows"] = len(rows)
    doc["unparseable_intents_lines"] = bad
    doc["postquote_rows"] = len(pq_rows)
    return doc


def build_reconciliation_section(doc: dict) -> list[str]:
    """Markdown for the doc: live evidence path vs replay, variant table."""
    v = doc["variants"]
    exact = v["exact"]
    lines = [
        "## RECONCILIATION — live evidence path vs replay",
        "",
        (f"Replayed **{doc['replayed_rows']}** live `skipped:no_drift_confirm` "
        f"decisions (intents files: {', '.join(doc['intents_files'])}) against "
        f"the postquote corpus ({doc['postquote_rows']} rows) under the "
        "engine's EXACT evidence path and one-leg-at-a-time relaxants."),
        "",
        ("Live evidence path being tested: `recent_postquote_moves` — same "
        "slug+us_side book, 120s horizon ±1s, rows ordered by WRITE-time "
        "ts_ns and visible strictly before the decision, last "
        f"{DRIFT_WINDOW} within the today+yesterday file window — then "
        f"`drift_confirmed`: mean of the visible samples >= "
        f"+{DRIFT_MIN_CONFIRM:.3f} ({100 * DRIFT_MIN_CONFIRM:.1f}pt), full "
        f"window of {DRIFT_WINDOW} required, fail-closed."),
        "",
        "| variant (one leg relaxed) | confirmed | n | empty evidence | <window | >=window | mean move of non-empty (pts) |",
        "|---|---|---|---|---|---|---|",
    ]
    labels = {
        "exact": "exact live rule",
        "no_window_cap": "drop the last-3 cap (all visible samples)",
        "no_2day_window": "drop the 2-day file window",
        "all_horizons": "pool 30/60/120s horizons",
        "bar_0.5pt": "bar 0.5pt instead of 1.0pt",
        "n_ge_1": "accept even a partial window (n>=1)",
    }
    for name in RECONCILE_VARIANTS:
        d = v[name]
        if not d["n"]:
            lines.append(f"| {labels[name]} | — | 0 | — | — | — | — |")
            continue
        lines.append(
            f"| {labels[name]} | {d['confirmed']}/{d['n']} "
            f"({100.0 * d['confirmed'] / d['n']:.1f}%) | {d['n']} | "
            f"{d['moves_empty']} | {d['moves_lt_window']} | "
            f"{d['moves_ge_window']} | {_fmt(d['mean_move_pts'])} |")
    exact_rate = (f"{100.0 * exact['confirmed'] / exact['n']:.1f}%"
                  if exact["n"] else "—")
    lines += [
        "",
        (f"Under the EXACT live rule the replay confirms "
        f"**{exact['confirmed']} of {exact['n']}** ({exact_rate} of replayed "
        "decisions) — see the closing paragraph for the verdict on "
        "strict-vs-unreachable."),
        "",
        ("Known asymmetry (relaxes the replay, if anything): the engine tails "
        "only the last 1MB of each day-file; the replay reads the whole file."),
        "",
    ]
    return lines


# ── winners oracle ───────────────────────────────────────────────────────────

def derive_winners() -> dict[str, dict]:
    """First-observation map winners from the GRID series-state capture
    (scripts/grid_winners.process_lines, the same oracle grid_winners.py --out
    emits), streamed over data/grid/series_state-*.jsonl."""
    from scripts.grid_winners import process_lines
    paths = sorted(glob.glob(os.path.join(_PROJECT_ROOT, "data", "grid",
                                          "series_state-*.jsonl")))
    def _lines():
        for p in paths:
            with open(p, encoding="utf-8", errors="replace") as fh:
                yield from fh
    result = process_lines(_lines())
    doc = result.get("winners", result) if isinstance(result, dict) else {}
    out = {}
    for key, rec in doc.items():
        if isinstance(rec, dict) and rec.get("winner"):
            out[str(key)] = {"winner": rec["winner"],
                             "loser": rec.get("loser")}
    return out


# ── report ───────────────────────────────────────────────────────────────────

def _fmt(x):
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.4f}"
    return str(x)


def build_report(doc: dict) -> str:
    f = doc["funnel"]
    p = doc["placement"]
    rate = lambda n, d: f"{100.0 * n / d:.1f}%" if d else "—"
    lines = [
        "# roundwin-v2 Pool B — drift-first accumulation (describe-only)",
        "",
        (f"Run: {doc['generated_utc']} · days: {', '.join(doc['params']['days']) or 'all'} · "
        f"design: docs/superpowers/specs/2026-08-28-roundwin-v2-design.md (§2.2 Pool B, §2.3 protocol)"),
        "",
        "ANALYSIS LANE ONLY — describe-only metrics, no registry edit, no",
        "arming, no trade path. Drift-first rule = the engine's own gate",
        (f"(DRIFT_WINDOW={doc['params']['drift_window']}, "
        f"DRIFT_MIN_CONFIRM={doc['params']['drift_min_confirm']}, fail-closed),"),
        "fed same-book oriented 120s moves strictly earlier than eval time.",
        "",
        "**Engine-faithful dedup order** (review fix 2026-08-31): the drift",
        "gate is evaluated over the FULL candidate population; the dedup slot",
        "is consumed ONLY on gate-pass placement (engine.py: self.seen.add",
        "runs on the placement path alone; drift-fail retries keep the key",
        "live). The earlier first-row-wins pre-gate dedup made the gate",
        "unsatisfiable and has been removed.",
        "",
        "## Funnel (record-only gates, non-consuming)",
        "",
        f"- mapentry rows loaded: {f['rows_loaded']}",
        f"- not qualifying: {f['not_qualifying']}",
        f"- no matched book (slug/side/bid): {f['no_book']}",
        f"- entry price out of range: {f['entry_price_out_of_range']}",
        (f"- **slate rows excluded** (recording-only, engine slate gate): "
        f"{f['slate_row']}"),
        (f"- **F1 leak filtered** (rounds_played+1 != round_number): "
        f"{f['f1_leak_filtered']}"),
        f"- **candidates (Pool B base rows): {f['candidates']}**",
        (f"- visible same-book evidence per candidate (engine window "
        f"{doc['params']['drift_window']}): "
        f"{json.dumps(doc['evidence_histogram'])}"),
        "",
        "## Placement pass (dedup consumed on gate-pass only)",
        "",
        f"- band-gated (p_map outside {US_BAND_GATE}): {p['band_gated']}",
        f"- drift unconfirmed (fail-closed): {p['no_drift_confirm']}",
        (f"- gate-passed duplicates (slot already consumed): "
        f"{p['duplicate_key_after_gate']}"),
        (f"- **placed (fills-equivalent): {p['placed']}** "
        f"({rate(p['placed'], f['candidates'])} of candidates)"),
        (f"- settled vs resolved map winners: {doc['gated']['metrics']['n']} "
        f"(excluded: {json.dumps(doc['gated']['excluded'])})"),
        "",
        "## Headline metrics (fills-equivalent, placed)",
        "",
        (f"- win rate: {_fmt(doc['gated']['metrics']['win_rate'])} "
        f"(95% series-clustered bootstrap CI "
        f"{_fmt_ci(doc['gated']['bootstrap']['win_rate_ci'])})"),
        (f"- mean edge: {_fmt(doc['gated']['metrics']['mean_edge'])} "
        f"(= {_fmt(doc['gated']['metrics'].get('mean_edge_pts'))} pts) "
        f"(95% CI {_fmt_ci(doc['gated']['bootstrap']['mean_edge_ci'])})"),
        (f"- n={doc['gated']['metrics']['n']} across "
        f"{doc['gated']['metrics'].get('series', 0)} series, days "
        f"{doc['gated']['metrics'].get('days', [])}"),
        "",
        "## Contrast — ungated (all candidates, no drift gate, no dedup)",
        "",
        (f"- win rate: {_fmt(doc['ungated']['metrics']['win_rate'])} "
        f"(95% CI {_fmt_ci(doc['ungated']['bootstrap']['win_rate_ci'])})"),
        (f"- mean edge: {_fmt(doc['ungated']['metrics']['mean_edge'])} "
        f"(95% CI {_fmt_ci(doc['ungated']['bootstrap']['mean_edge_ci'])})"),
        f"- n={doc['ungated']['metrics']['n']}",
        "",
        "## By day (placed)",
        "",
        "| day | n | win rate | mean edge (pts) |",
        "|---|---|---|---|",
    ]
    for d, m in doc["gated"]["by_day"].items():
        lines.append(f"| {d} | {m['n']} | {_fmt(m['win_rate'])} | "
                     f"{_fmt(m.get('mean_edge_pts'))} |")
    if doc.get("reconciliation"):
        lines += build_reconciliation_section(doc["reconciliation"])
    lines += [
        "",
        "## Caveats",
        "",
        "- Describe-only: no LightGBM is trained here (training is §4, requires",
        "  registry pre-registration H-ROUNDWINV2 first — iron rail 2).",
        "- Drift evidence = the engine's own lookup (recent_postquote_moves",
        "  semantics): same (slug, us_side) book, 120s horizon ±1s, rows",
        "  WRITTEN strictly before the decision (a 120s sample materializes",
        "  ~120s after its eval, so an eval's own outcome can never be its",
        "  own evidence), last 3 within the today+yesterday file window.",
        "- Engine-faithful dedup means a book's drift history ACCUMULATES",
        "  across its rejected crossings (each drift-fail keeps the key",
        "  live), so later crossings CAN confirm — unlike the old",
        "  first-row-wins replay where every book's only candidate was its",
        "  history-less first crossing.",
        "- Winners are the first-observation GRID oracle; entries on series whose",
        "  capture has no finished map are excluded (counted), never guessed.",
        "",
    ]
    return "\n".join(lines)


def _fmt_ci(ci):
    if not ci:
        return "—"
    return f"[{ci[0]:.4f}, {ci[1]:.4f}]"


def _nonneg_int(raw: str) -> int:
    """argparse type: --bootstrap needs a 0 lower bound (negative resample
    counts are a caller error, not a runtime crash)."""
    try:
        v = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {raw!r}")
    if v < 0:
        raise argparse.ArgumentTypeError(
            f"--bootstrap must be >= 0 (got {v})")
    return v


def write_doc_atomic(path: str, report: str) -> None:
    """Atomic --out-doc write: tmp file in the SAME directory, fsync, then
    os.replace — a crash mid-write never leaves a torn report behind."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(report)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", default="",
                    help="comma-separated capture days (default: all)")
    ap.add_argument("--winners", default="",
                    help="grid_winners --out JSON (default: derive from data/grid)")
    ap.add_argument("--out-doc",
                    default=os.path.join(
                        # Env-overridable so the test suite writes under tmp_path
                        # instead of spraying a report into the real data/analysis/.
                        os.environ.get("GRIDBOT_POOLEB_OUT_DIR",
                                       os.path.join(_PROJECT_ROOT, "data", "analysis")),
                        "roundwin2-poolb-{}.md".format(datetime.datetime.now(
                            datetime.UTC).strftime("%Y%m%dT%H%M%SZ"))),
                    help="markdown report path (default: timestamped file "
                         "under data/analysis/, which is gitignored)")
    ap.add_argument("--write-doc", action="store_true",
                    help="write the report to the canonical docs/ path "
                         "(docs/analysis/2026-08-31-roundwin2-poolb.md); "
                         "default runs never touch docs/")
    ap.add_argument("--bootstrap", type=_nonneg_int, default=BOOTSTRAP_B)
    ap.add_argument("--no-reconcile", action="store_true",
                    help="skip the live-ledger reconciliation section "
                         "(used by tests; the real corpus is not test input)")
    ap.add_argument("--reconcile-days", default="",
                    help="comma-separated intents days to replay "
                         "(default: all data/executor/intents-*.jsonl)")
    args = ap.parse_args(argv)

    if args.winners and not os.path.exists(args.winners):
        print(f"roundwin2_poolb: --winners file not found: {args.winners}",
              file=sys.stderr)
        return 2

    days = [d for d in args.days.split(",") if d] or None
    me_rows, me_bad = load_jsonl(day_files("mapentry", days))
    pq_rows, pq_bad = load_jsonl(day_files("postquote", days))

    # Record-only gates (NO dedup here): slate exclusion, F1, book/price.
    candidates, counters = select_poolb_entries(me_rows)
    hist = book_history(pq_rows)
    # The drift gate sees the FULL candidate population — engine-faithful:
    # drift-fail retries keep the dedup key live.
    candidates = apply_drift_gate(candidates, hist)
    # Dedup is consumed ONLY on gate-pass placement (engine.py self.seen.add).
    placed, placement = place_with_dedup(candidates)

    winners = (load_winners(args.winners)[0] if args.winners
               else derive_winners())
    gated_settled, gated_excl = settle_entries(placed, winners)
    ung_settled, ung_excl = settle_entries(candidates, winners)

    def block(sub_entries, settled, excluded):
        return {
            "n": len(sub_entries),
            "pass_rate": (len(sub_entries) / len(candidates))
            if candidates else None,
            "evidence": evidence_histogram(sub_entries),
            "metrics": _metrics(settled),
            "excluded": excluded,
            "bootstrap": series_cluster_bootstrap(settled, b=args.bootstrap),
            "by_day": {
                d: _metrics([s for s in settled if day_of(s) == d])
                for d in sorted({day_of(s) for s in settled})
            },
        }

    doc = {
        "generated_utc": datetime.datetime.now(
            datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "params": {
            "days": days or sorted({os.path.basename(p).split("-")[-1][:8]
                                    for p in day_files("mapentry", None)}),
            "drift_window": DRIFT_WINDOW,
            "drift_min_confirm": DRIFT_MIN_CONFIRM,
            "bootstrap_b": args.bootstrap,
            "winners_source": "file" if args.winners else "derived",
        },
        "funnel": counters,
        "placement": placement,
        "unparseable_lines": {"mapentry": me_bad, "postquote": pq_bad},
        "postquote_books": len(hist),
        "evidence_histogram": evidence_histogram(candidates),
        "gated": block(placed, gated_settled, gated_excl),
        "ungated": block(candidates, ung_settled, ung_excl),
    }

    if not args.no_reconcile:
        rdays = [d for d in args.reconcile_days.split(",") if d] or None
        doc["reconciliation"] = reconcile(rdays)

    report = build_report(doc)
    out_path = args.out_doc
    if args.write_doc:
        # Explicit operator opt-in: overwrite the canonical docs/ report
        # (exact current path/content behavior). GRIDBOT_POOLEB_DOCS_REPORT
        # is a test-only override of that path.
        out_path = os.environ.get("GRIDBOT_POOLEB_DOCS_REPORT",
                                  DOCS_REPORT_PATH)
    if out_path:
        write_doc_atomic(out_path, report)

    print(json.dumps(doc, indent=2, sort_keys=True))
    g = doc["gated"]["metrics"]
    print(
        f"roundwin2_poolb: candidates={counters['candidates']} "
        f"slate_excluded={counters['slate_row']} "
        f"f1_filtered={counters['f1_leak_filtered']} "
        f"placed={placement['placed']} settled={g['n']} "
        f"win_rate={_fmt(g['win_rate'])} mean_edge_pts="
        f"{_fmt(g.get('mean_edge_pts'))} "
        f"doc={args.out_doc or 'none'}",
        file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
