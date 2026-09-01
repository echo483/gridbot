"""H-MAPENTRY1 against its frozen bar — the verdict path the lane never had.

`gridbot.instruments.verdict` reads only shadow INSTANCE rows, so H-MAPENTRY1
— the one lane the executor can ever arm — appeared in no verdict row at all
and its bar was not computable by any code in the tree. This module is that
missing path.

THE FROZEN BAR (docs/experiments/registry.jsonl, H-MAPENTRY1 — verbatim intent):
FALSIFIED on >=150 held-out resolved instances carrying a PMUS book at
detection if ANY of:
  (i)   Brier(P_map) >= Brier(book price as forecast) on the same instances
  (ii)  ECE(P_map) > 0.08 (QUINTILE bins)
  (iii) at >=60 qualifying entries, mean settled edge <= +2 points
        OR the by-series bootstrap 95% CI includes 0
STARVATION: <5% of resolved instances ever showing a qualifying quote closes
the lane.

Design notes that are load-bearing:

* The MAP-winner oracle is `scripts/grid_winners.py`, invoked as a subprocess
  so this stays a pure consumer of the frozen resolution rules (MR12-validated,
  ambiguous finals excluded). `verdict.build_oracles` is a ROUND-winner oracle
  keyed (map, round) and CANNOT substitute for it.
* Rows are not independent: thousands of rows come from tens of series and
  share a match outcome. Every interval here resamples SERIES, not rows.
  Row-level pooling is what made earlier p_map headlines unstable.
* The bar says "quintile bins". That is five bins, not the ten
  `scripts/pmap_calibration.py` reports. Both are returned; only the quintile
  number is the bar.
* Queue-16 (a4d469f) added full-slate coverage rows that score far worse than
  detector rows. They are recording-only, so they are SPLIT OUT, never pooled.

This module is REPORTING ONLY. It reads no engine state and changes no gate.

COST NOTE (measured 2026-08-30): load_map_winners re-parses every
series_state file — 10.4s over 1.22 GiB. `verdict.compact_summary` is called
by morning_report, eod_report and analysis/daily, so that cost lands in three
nightly jobs and grows linearly with a corpus that gains ~250 MB/day forever.
Bounding the oracle to date-matched files was considered and REJECTED: a
series starting 23:50 and resolving 00:10 spans two capture files, so
date-matching can silently drop winners — a correctness risk traded for a
perf win we do not need at 10s. Revisit with a content-addressed cache
(key on file mtime+size) if the nightly jobs ever get tight, never with a
date filter.
"""

from __future__ import annotations

import glob as _glob
import json
import random
import re
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from gridbot.jsonl import iter_jsonl

US_TICK = 0.01
CEILING = 0.90
ENTRY_MARGIN = 0.04

# Frozen bar constants — do not tune. These are the registry's numbers.
BAR_ECE_MAX = 0.08
BAR_MIN_RESOLVED = 150
BAR_MIN_QUALIFYING = 60
BAR_MIN_EDGE_PTS = 0.02  # "+2 points"
BAR_STARVATION_FRACTION = 0.05

# H-MAPENTRY1-BOOKPIN1 (registry.jsonl, 2026-08-31): the bar (i) comparator
# "book price as forecast" is PINNED to book MID on resolved instances carrying
# a real two-sided PMUS book; side_ask is execution-only, never the bar
# comparator. The same row pre-registers the temporal holdout below. Reporting
# layer only — this changes no computation, only how the verdict is annotated.
BOOKPIN_HYPOTHESIS_ID = "H-MAPENTRY1-BOOKPIN1"
BOOKPIN_PINNED_READING = "mid"
# Actual holdout start carried by H-MAPENTRY1-BOOKPIN1 ("first GRID<->PMUS
# mapping row recorded at or after <ts>"). Used only if the registry row
# cannot be parsed — the registry text is the source of truth.
HOLD_START_FALLBACK = "2026-08-31T19:53:46Z"

_ROOT = Path(__file__).resolve().parents[3]


# --------------------------------------------------------------------------
# loading


def load_mapentry_rows(pattern: str) -> list[dict]:
    """Every mapentry evaluation row matching `pattern`.

    D1: these live in data/shadow/mapentry-*.jsonl with type=="mapentry".
    `verdict.load_instances` globs instances-*.jsonl AND filters
    type=="instance", so it excludes them twice over.
    """
    out: list[dict] = []
    for path in sorted(_glob.glob(pattern)):
        for rec in iter_jsonl(path):
            if rec.get("type") == "mapentry":
                out.append(rec)
    return out


def load_map_winners(state_pattern: str) -> dict:
    """Map winners from the frozen oracle script (subprocess = pure consumer)."""
    files = sorted(_glob.glob(state_pattern))
    if not files:
        return {}
    res = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "grid_winners.py"), *files],
        capture_output=True,
        text=True,
        check=False,  # returncode handled explicitly below
    )
    if res.returncode != 0:
        raise RuntimeError(f"grid_winners failed: {res.stderr[-400:]}")
    return json.loads(res.stdout).get("winners", {})


def provenance(row: dict) -> str:
    """DETECTOR (the original population) vs SLATE (Queue-16 coverage rows)."""
    if "SLATE" in str(row.get("src_hypothesis_id") or ""):
        return "SLATE"
    if row.get("dedup_extra") == "slate":
        return "SLATE"
    return "DETECTOR"


def has_two_sided_book(row: dict) -> bool:
    """A real two-sided PMUS book at detection (D3 semantics: never impute).

    One-sided, missing, zeroed or crossed books do not qualify — the same
    population rule clause (i) uses, factored out so the holdout count can
    share it without touching the scoring path.
    """
    bid, ask = row.get("bid"), row.get("ask")
    if not isinstance(bid, (int, float)) or not isinstance(ask, (int, float)):
        return False
    bid, ask = float(bid), float(ask)
    return 0.0 < bid < 1.0 and 0.0 < ask < 1.0 and ask > bid


def iso_to_ns(iso: str) -> int:
    """Epoch nanoseconds for an ISO-8601 timestamp (Z suffix accepted)."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1_000_000_000)


def holdout_start_iso(registry_path: str | Path | None = None) -> str:
    """The holdout start declared by H-MAPENTRY1-BOOKPIN1 in registry.jsonl.

    Parses the row's own text ("recorded at or after <ts>"). Falls back to the
    actual value the row carries as of registration if the file is missing or
    unparseable, so the reporting layer never crashes on the registry.
    """
    path = Path(registry_path) if registry_path else (
        _ROOT / "docs" / "experiments" / "registry.jsonl"
    )
    try:
        for rec in iter_jsonl(path):
            if rec.get("hypothesis_id") != BOOKPIN_HYPOTHESIS_ID:
                continue
            m = re.search(
                r"at or after\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)",
                str(rec.get("hypothesis_text", "")),
            )
            if m:
                return m.group(1)
    except OSError:
        pass
    return HOLD_START_FALLBACK


# --------------------------------------------------------------------------
# orientation and outcomes


def map_outcome(row: dict, winners: dict) -> float | None:
    """1.0 if the row's team won its map, 0.0 if it lost, None otherwise.

    D6: a team matching NEITHER side is excluded, never guessed.
    """
    w = winners.get(f"{row.get('series_id')}:{row.get('map_number')}")
    if not w:
        return None
    team = row.get("team")
    if team == w.get("winner"):
        return 1.0
    if team == w.get("loser"):
        return 0.0
    return None


def book_forecast(row: dict, reading: str = "mid") -> float | None:
    """The book's implied probability FOR OUR SIDE, or None if it has no book.

    D3: one-sided, missing, zeroed or crossed books are not in clause (i)'s
    population ("instances carrying a PMUS book at detection") — never impute.
    D5: a short buys the No leg, so its probability is 1 - mid.

    `reading` selects how the registry's "book price" is read: "mid" is the
    fair reading; "side_ask" is the price we would transact at.
    """
    if reading == "side_ask":
        sa = row.get("side_ask")
        if not isinstance(sa, (int, float)) or not 0.0 < float(sa) < 1.0:
            return None
        return float(sa)

    bid, ask = row.get("bid"), row.get("ask")
    if not isinstance(bid, (int, float)) or not isinstance(ask, (int, float)):
        return None
    bid, ask = float(bid), float(ask)
    if not (0.0 < bid < 1.0 and 0.0 < ask < 1.0) or ask <= bid:
        return None
    mid = (bid + ask) / 2.0
    us = row.get("us_side")
    if us == "long":
        return mid
    if us == "short":
        return 1.0 - mid
    return None


def posted_price(row: dict) -> float | None:
    """The price the engine would post, or None if the row cannot price."""
    ask, cap = row.get("side_ask"), row.get("entry_price_max")
    if not isinstance(ask, (int, float)) or not isinstance(cap, (int, float)):
        return None
    return round(min(float(ask) - US_TICK, float(cap), CEILING), 4)


def dedupe_per_book(rows: list[dict]) -> list[dict]:
    """One entry per (series, map, side) — the engine's dedup key.

    D4: the qualifying population counts the same map book once per
    re-qualifying round. Using it as n inflates every edge estimate. First
    observation wins, matching the engine's one-entry-per-book rule.
    """
    seen: dict[tuple, dict] = {}
    for row in sorted(rows, key=lambda r: (r.get("ts_ns") or 0)):
        key = (row.get("series_id"), row.get("map_number"), row.get("us_side"))
        seen.setdefault(key, row)
    return list(seen.values())


# --------------------------------------------------------------------------
# statistics — series-clustered throughout


def ece(pairs: list[tuple[float, float]], bins: int = 5) -> float | None:
    """Expected calibration error over equal-width bins, midpoint convention.

    Default is FIVE bins: the registry pins "ECE(P_map) > 0.08 (quintile
    bins)". A ten-bin number is a different statistic and is not the bar.
    """
    if not pairs:
        return None
    buckets: dict[int, list] = defaultdict(lambda: [0, 0.0])
    for p, y in pairs:
        i = min(int(p * bins), bins - 1)
        buckets[i][0] += 1
        buckets[i][1] += y
    total = len(pairs)
    out = 0.0
    for i, (cnt, wins) in buckets.items():
        predicted = (i + 0.5) / bins
        actual = wins / cnt
        out += cnt / total * abs(predicted - actual)
    return out


def _brier(pairs: list[tuple[float, float]]) -> float | None:
    if not pairs:
        return None
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def bootstrap_by_series(
    by_series: dict[str, list], stat, iters: int = 2000, seed: int = 13
) -> tuple[float, float] | None:
    """95% interval for `stat` under resampling of SERIES, not rows."""
    series = list(by_series)
    if not series:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(iters):
        sample: list = []
        for _ in series:
            sample.extend(by_series[rng.choice(series)])
        v = stat(sample)
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    vals.sort()
    return vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals))]


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


# --------------------------------------------------------------------------
# the bar


def evaluate_bar(
    rows: list[dict],
    winners: dict,
    reading: str = "mid",
    include_slate: bool = False,
    holdout_start: str | None = None,
) -> dict:
    """Score H-MAPENTRY1's recording population against its three frozen clauses.

    `rows` is the RECORDING population (D7: engine band/drift/slate gates are
    NOT verdict filters — the registry's ENGINE_GATE_AMENDMENT says so in
    terms). `include_slate` pools Queue-16 coverage rows; default is to split
    them out. `holdout_start` overrides the holdout start declared by
    H-MAPENTRY1-BOOKPIN1 (default: parse it from registry.jsonl).
    """
    pop = [r for r in rows if include_slate or provenance(r) == "DETECTOR"]

    resolved: list[dict] = []
    for row in pop:
        y = map_outcome(row, winners)
        p = row.get("p_map")
        if y is None or not isinstance(p, (int, float)):
            continue
        resolved.append({**row, "_y": float(y), "_p": float(p)})

    qualifying = [r for r in resolved if r.get("qualifies")]

    # --- clause (i): model vs the book, on rows carrying a book -------------
    with_book = [
        (r, b)
        for r in resolved
        if (b := book_forecast(r, reading)) is not None
    ]
    model_pairs = [(r["_p"], r["_y"]) for r, _ in with_book]
    book_pairs = [(b, r["_y"]) for r, b in with_book]
    bm, bb = _brier(model_pairs), _brier(book_pairs)
    diff_by_series: dict[str, list] = defaultdict(list)
    for r, b in with_book:
        diff_by_series[r["series_id"]].append(
            ((r["_p"] - r["_y"]) ** 2) - ((b - r["_y"]) ** 2)
        )
    diff_ci = bootstrap_by_series(diff_by_series, _mean)
    clause_i = {
        "n": len(with_book),
        "reading": reading,
        "brier_model": None if bm is None else round(bm, 4),
        "brier_book": None if bb is None else round(bb, 4),
        "difference": None if (bm is None or bb is None) else round(bm - bb, 4),
        "difference_ci": None if diff_ci is None else (round(diff_ci[0], 4), round(diff_ci[1], 4)),
        # bar text: Brier(P_map) >= Brier(book) FALSIFIES
        "breached": None if (bm is None or bb is None) else bool(bm >= bb),
        "breached_robustly": bool(diff_ci is not None and diff_ci[0] > 0),
    }

    # --- clause (ii): ECE, quintile bins ------------------------------------
    # POPULATION: the bar scopes itself to "resolved instances carrying a PMUS
    # book at detection" and only narrows to "qualifying entries" in clause
    # (iii). So (ii) shares (i)'s population. The qualifying-subset ECE is the
    # operationally interesting number and is reported as reference, but it is
    # NOT the bar.
    bar_rows = [r for r, _ in with_book]
    bar_pairs = [(r["_p"], r["_y"]) for r in bar_rows]
    e5 = ece(bar_pairs, bins=5)
    e10 = ece(bar_pairs, bins=10)
    ece_by_series: dict[str, list] = defaultdict(list)
    for r in bar_rows:
        ece_by_series[r["series_id"]].append((r["_p"], r["_y"]))
    ece_ci = bootstrap_by_series(ece_by_series, lambda s: ece(s, bins=5))
    qual_pairs = [(r["_p"], r["_y"]) for r in qualifying]
    clause_ii = {
        "n": len(bar_rows),
        "ece_quintile": None if e5 is None else round(e5, 4),
        "ece_10bin_reference": None if e10 is None else round(e10, 4),
        "ece_ci": None if ece_ci is None else (round(ece_ci[0], 4), round(ece_ci[1], 4)),
        "bar": BAR_ECE_MAX,
        "breached": None if e5 is None else bool(e5 > BAR_ECE_MAX),
        "breached_robustly": bool(ece_ci is not None and ece_ci[0] > BAR_ECE_MAX),
        # reference only — the population the engine actually trades
        "n_qualifying_reference": len(qualifying),
        "ece_qualifying_reference": (
            None if not qual_pairs else round(ece(qual_pairs, bins=5), 4)
        ),
    }

    # --- clause (iii): settled edge on the DEDUPED population ---------------
    deduped = dedupe_per_book(qualifying)
    edge_by_series: dict[str, list] = defaultdict(list)
    for r in deduped:
        price = posted_price(r)
        if price is None:
            continue
        edge_by_series[r["series_id"]].append(r["_y"] - price)
    edges = [e for es in edge_by_series.values() for e in es]
    edge_ci = bootstrap_by_series(edge_by_series, _mean)
    mean_edge = _mean(edges)
    clause_iii = {
        "n_deduped": len(deduped),
        "n_priced": len(edges),
        "n_qualifying_rows": len(qualifying),
        "mean_edge": None if mean_edge is None else round(mean_edge, 4),
        "edge_ci": None if edge_ci is None else (round(edge_ci[0], 4), round(edge_ci[1], 4)),
        "bar_min_edge": BAR_MIN_EDGE_PTS,
        "breached": (
            None
            if mean_edge is None
            else bool(
                mean_edge <= BAR_MIN_EDGE_PTS
                or (edge_ci is not None and edge_ci[0] <= 0 <= edge_ci[1])
            )
        ),
    }

    # --- starvation ---------------------------------------------------------
    frac = (len(qualifying) / len(resolved)) if resolved else None
    starvation = {
        "resolved": len(resolved),
        "qualifying": len(qualifying),
        "qualifying_fraction": None if frac is None else round(frac, 4),
        "bar": BAR_STARVATION_FRACTION,
        "starved": None if frac is None else bool(frac < BAR_STARVATION_FRACTION),
    }

    # --- holdout (H-MAPENTRY1-BOOKPIN1) --------------------------------------
    # Pre-registered temporal holdout: resolved instances carrying a real
    # two-sided PMUS book, recorded at/after the declared start. The registry
    # makes the >=150 INSTANCE threshold binding (series are appended until it
    # is met), so this count is over instances, not series.
    hold_start_iso = holdout_start or holdout_start_iso()
    hold_start_ns = iso_to_ns(hold_start_iso)
    holdout_n = sum(
        1
        for r in resolved
        if has_two_sided_book(r)
        and isinstance(r.get("ts_ns"), (int, float))
        and r["ts_ns"] >= hold_start_ns
    )
    holdout_ready = holdout_n >= BAR_MIN_RESOLVED
    holdout = {
        "start": hold_start_iso,
        "n": holdout_n,
        "bar": BAR_MIN_RESOLVED,
        "holdout_declared": holdout_ready,
    }

    # --- status -------------------------------------------------------------
    powered = (
        len(resolved) >= BAR_MIN_RESOLVED and len(deduped) >= BAR_MIN_QUALIFYING
    )
    breaches = [
        c["breached"]
        for c in (clause_i, clause_ii, clause_iii)
        if c["breached"] is not None
    ]
    if not powered:
        status = "UNDER_POWERED"
    elif any(breaches):
        status = "FALSIFIED_ON_CAPTURED_DATA"
    else:
        status = "NOT_FALSIFIED"

    return {
        "hypothesis_id": "H-MAPENTRY1",
        "population": "DETECTOR+SLATE" if include_slate else "DETECTOR",
        "status": status,
        "clause_i": clause_i,
        "clause_ii": clause_ii,
        "clause_iii": clause_iii,
        "starvation": starvation,
        # A registry verdict requires the pre-declared holdout window to be
        # FULL. H-MAPENTRY1-BOOKPIN1 declares it; holdout_declared flips true
        # only at >=150 held-out two-sided-book resolved instances, never
        # before — no verdict is rendered on the pre-holdout corpus.
        "holdout": holdout,
        "verdict_reachable": bool(powered),
        "holdout_declared": holdout_ready,
        "notes": [
            "Reporting layer. The registry bar is the only PASS/FAIL rule.",
            "All intervals resample SERIES, not rows.",
            (
                "clause (ii) uses QUINTILE bins per the registry text; the "
                "10-bin number is reference only."
            ),
            "clause (iii) n is the DEDUPED per-book count, not qualifying rows.",
            (
                "Holdout per H-MAPENTRY1-BOOKPIN1: the registry verdict "
                "renders only on held-out instances recorded at/after the "
                "declared start, once holdout_declared=true (>=150). Until "
                "then, status is a measurement over captured rows."
            ),
            (
                "BOOKPIN per H-MAPENTRY1-BOOKPIN1: the clause (i) comparator "
                "'book price as forecast' is the book MID; side_ask is the "
                "execution price for edge accounting only, never the bar."
            ),
        ],
    }


def summary_lines(result: dict) -> list[str]:
    """Compact human lines for the verdict table and the nightly report."""
    ci, cii, ciii, st = (
        result["clause_i"],
        result["clause_ii"],
        result["clause_iii"],
        result["starvation"],
    )

    def mark(v):
        return "BREACH" if v else ("ok" if v is not None else "n/a")

    # H-MAPENTRY1-BOOKPIN1: make the pinned comparator explicit on the block.
    # Any other reading is surfaced as a DEVIATION — side_ask is the execution
    # price for edge accounting only, never the bar (i) comparator.
    if ci["reading"] == BOOKPIN_PINNED_READING:
        pin_note = "[BOOKPIN: book=mid reading]"
    else:
        pin_note = (
            f"[BOOKPIN DEVIATION: reading={ci['reading']} is execution-only; "
            "pinned bar comparator is book=mid per H-MAPENTRY1-BOOKPIN1]"
        )

    lines = [
        f"  H-MAPENTRY1 [{result['population']}] {result['status']} {pin_note}",
        (
            f"    (i)   Brier model {ci['brier_model']} vs book {ci['brier_book']} "
            f"({ci['reading']}, n={ci['n']}) diff {ci['difference']} "
            f"CI {ci['difference_ci']} -> {mark(ci['breached'])}"
        ),
        (
            f"    (ii)  ECE quintile {cii['ece_quintile']} CI {cii['ece_ci']} "
            f"bar {cii['bar']} (n={cii['n']}; 10-bin ref {cii['ece_10bin_reference']}, "
            f"qualifying-subset ref {cii['ece_qualifying_reference']} "
            f"n={cii['n_qualifying_reference']}) -> {mark(cii['breached'])}"
        ),
        (
            f"    (iii) edge {ciii['mean_edge']} CI {ciii['edge_ci']} "
            f"n_deduped {ciii['n_deduped']}/{BAR_MIN_QUALIFYING} "
            f"(from {ciii['n_qualifying_rows']} rows) -> {mark(ciii['breached'])}"
        ),
        (
            f"    starv {st['qualifying_fraction']} of {st['resolved']} resolved "
            f"(bar <{st['bar']}) -> {mark(st['starved'])}"
        ),
    ]

    # H-MAPENTRY1-BOOKPIN1 holdout status: PARTIAL until the held-out
    # two-sided-book resolved population reaches the frozen >=150; only then
    # is holdout_declared=true and a registry verdict renderable.
    ho = result.get("holdout")
    if ho is not None:
        if ho.get("holdout_declared"):
            lines.append(
                f"    holdout: READY (n={ho['n']} >= {ho['bar']} held-out, "
                "holdout_declared=true)"
            )
        else:
            lines.append(
                f"    holdout: PARTIAL (n={ho['n']} < {ho['bar']}, "
                "holdout_declared=false)"
            )
    return lines


def run(
    mapentry_pattern: str | None = None,
    state_pattern: str | None = None,
    reading: str = "mid",
    include_slate: bool = False,
) -> dict | None:
    """Load from the corpus and score. None when there is nothing to score."""
    from gridbot import config

    mapentry_pattern = mapentry_pattern or str(
        config.DATA_DIR / "shadow" / "mapentry-*.jsonl"
    )
    state_pattern = state_pattern or str(
        config.DATA_DIR / "grid" / "series_state-*.jsonl"
    )
    rows = load_mapentry_rows(mapentry_pattern)
    if not rows:
        return None
    winners = load_map_winners(state_pattern)
    if not winners:
        return None
    return evaluate_bar(rows, winners, reading=reading, include_slate=include_slate)
