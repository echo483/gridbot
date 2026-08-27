#!/usr/bin/env python3
"""
entry_quality.py — Stratify settled WOULD-TRADE mapentry entries by quality dimension.

Analysis only (slow loop, iron rail 1): consumes mapentry JSONL day files plus the
settled map-winner oracle (scripts/grid_winners.py --out shape) and produces the
evidence numbers for TWO pre-registered policy amendments (Queue 13 entry-gate
loosening). Numbers only — no recommendations; nothing here touches orders, keys,
the network, or the trade path.

POPULATION (engine dedup mirror): every mapentry row with qualifies==true, a
numeric unit-interval side_ask and p_map, a non-empty market_slug, us_side in
{"long","short"}, an identity-safe (series_id, map_number), an int ts_ns, an int
round_number, a non-empty team, and a numeric entry_price_max — keeping the FIRST
such row per (series_id, map_number, market_slug, us_side), exactly the dedup the
executor applies at intent time (src/gridbot/executor/intents.py
build_us_intent). This is the WOULD-TRADE population, not just actual fills;
actual fills (--fills) are a tagged subset of it.

PER-EVALUATION FIELDS (after the settled verdict):
  entry_price   = round(min(side_ask - 0.01, entry_price_max, 0.90), 4) — the live
                  engine rule (US_TICK=0.01, DEFAULT_ENTRY_CEILING=0.90); rows
                  whose clamped price falls outside the open interval (0, 1) are
                  excluded ("entry_price_out_of_range") and do NOT consume their
                  dedup key (a degenerate price never built an order).
  discount_pts  = (p_map - entry_price) * 100, rounded to 6dp before bucketing:
                  entry_price is 4dp, so anything beyond 6dp is binary float
                  noise; without the rounding (0.70 - 0.60) * 100 lands at
                  9.999999999999998 and the "exactly 10.0 -> 10to20" boundary is
                  unreproducible.
  band          = "core" 0.50<=p_map<=0.80 | "shoulder" 0.40<=p_map<=0.90 |
                  "outside" otherwise — the engine band-gate classification
                  (frozen gate vs the widened 2026-08-27 test gate); reported as
                  a stratum because the loosening amendment is exactly a
                  shoulder-vs-core edge question.
  phase         = "early" round_number<=5 | "mid" <=15 | "late" otherwise.
  shock_pre     = True iff on the SAME (series_id, map_number, team) sequence
                  some pair of consecutive valid-p_map rows with BOTH rows inside
                  the inclusive window [ts_ns - shock_window, ts_ns] has
                  |delta p_map| >= --shock. The jump INTO the evaluation row
                  itself counts (it is realized strictly before the entry
                  decision); int-ns arithmetic only. Sequences are built from ALL
                  valid-p_map mapentry rows, qualifying or not — the model path
                  is the same either way.
  side          = us_side.  settled edge per eval = won - entry_price.

SEMANTIC DECISIONS (pinned by tests):
  - Dedup happens BEFORE verdict lookup: the engine dedupes at intent time, when
    outcomes do not exist yet, so the first qualifying row consumes the key even
    if it later settles as no_verdict.
  - The winner comes ONLY from the winners file, compared as exact strings; it is
    never inferred from scores or team order (iron rail 2; mirrors
    wave_edge_join).
  - The p_map stratum carries a fifth "outside" bucket (p_map < 0.40 or > 0.90)
    so every settled evaluation lands in exactly one bucket and bucket n's
    reconcile to n.

OUTPUT (stdout JSON; ONE summary line on stderr):
  {"n": settled evaluations,
   "excluded": {reason: count, ...}          # all reason keys always present
   "strata": {"discount": {lt5|5to10|10to20|gte20: M},
              "p_map": {"0.40-0.55"|"0.55-0.70"|"0.70-0.80"|"0.80-0.90"|
                        "outside": M},
              "phase": {early|mid|late: M},
              "shock_pre": {"true"|"false": M},
              "side": {"long"|"short": M},
              "band": {core|shoulder|outside: M},
              "discount_x_phase": {<discount bucket>: {<phase bucket>: M}}},
   "fills_subset": null | {"n": tagged settled, "strata": <same strata shape>},
   "params": {...}}
  where M = {"n", "win_rate", "mean_edge", "total_edge"}; an empty stratum
  reports n 0 and null metrics.

Usage:
    uv run python scripts/entry_quality.py MAPENTRY.jsonl [MORE.jsonl ...] \
        --winners WINNERS.json [--fills FILLS.json] [--shock 0.25] \
        [--shock-window-s 600] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Any

__all__ = [
    "DEFAULT_SHOCK",
    "DEFAULT_SHOCK_WINDOW_S",
    "US_TICK",
    "ENTRY_CEILING",
    "entry_price",
    "discount_pts",
    "discount_bucket",
    "p_map_bucket",
    "phase_bucket",
    "band_bucket",
    "load_mapentry",
    "load_winners",
    "load_fills",
    "build_shock_sequences",
    "has_pre_shock",
    "select_candidates",
    "settle_candidates",
    "compute_strata",
    "main",
]

DEFAULT_SHOCK = 0.25
DEFAULT_SHOCK_WINDOW_S = 600
# Live engine constants — literal mirrors drift (replay_harness did; this
# file did too: its old 0.90 ignored per-hypothesis ceilings and its price
# check admitted sub-tick prices the engine refuses).
from gridbot.executor.intents import (  # noqa: E402
    DEFAULT_ENTRY_CEILING, US_BAND_GATE, US_TICK,
)
ENTRY_CEILING = DEFAULT_ENTRY_CEILING  # live engine constant — a literal mirror drifted in replay_harness once already
NS_PER_S = 1_000_000_000

# Maximum bytes scanned per mapentry line before it is treated as corrupt
# (mirrors wave_edge_join.py; real capture rows are ~500 bytes).
MAX_LINE_BYTES = 1024 * 1024

# Exclusion reason codes (single source of truth; the excluded dict carries ALL
# of them with zero counts so its shape is stable across runs).
REASON_UNPARSEABLE = "unparseable_lines"
REASON_MALFORMED = "malformed_row"
REASON_PRICE = "entry_price_out_of_range"
REASON_DUPLICATE = "duplicate_key"
REASON_NO_VERDICT = "no_verdict"
REASON_NOT_IN_VERDICT = "team_not_in_verdict"
EXCLUDED_REASONS = (
    REASON_UNPARSEABLE,
    REASON_MALFORMED,
    REASON_PRICE,
    REASON_DUPLICATE,
    REASON_NO_VERDICT,
    REASON_NOT_IN_VERDICT,
)

DISCOUNT_BUCKETS = ("lt5", "5to10", "10to20", "gte20")
PMAP_BUCKETS = ("0.40-0.55", "0.55-0.70", "0.70-0.80", "0.80-0.90", "outside")
PHASE_BUCKETS = ("early", "mid", "late")
SHOCK_BUCKETS = ("true", "false")
SIDE_BUCKETS = ("long", "short")
BAND_BUCKETS = ("core", "shoulder", "outside")


# ── helpers (adapted from scripts/wave_edge_join.py) ───────────────────────

def _safe_float(v: Any) -> float | None:
    """Coerce a numeric field to float, returning None on missing/bad values.

    Accepts ints, floats, and decimal strings; rejects booleans, non-finite
    values, and coercion failures.  OverflowError is caught because JSON
    integers are unbounded: float(10**400) raises OverflowError rather than
    ValueError, and one such field must never crash the run (hard rule:
    malformed rows are skipped + counted).
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        fv = float(v)
        if math.isfinite(fv):
            return fv
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def _is_unit_interval(x: float | None) -> bool:
    """True iff x is not None and lies within [0, 1]."""
    return x is not None and 0.0 <= x <= 1.0


def _identity_element(v: Any) -> bool:
    """Strict type gate for one identity element.

    Identity fields participate in dict keys and are compared by equality, but
    Python unifies hash/equality across int, float, and bool (100 == 100.0 ==
    True).  Without this gate an upstream schema drift from int ts_ns to float
    ts_ns would SILENTLY weld two different data points together.  Bools are
    rejected because True == 1 would alias map_number 1.  Strings stay strings.
    """
    return isinstance(v, (str, int)) and not isinstance(v, bool)


# ── the live engine entry rule (mirror of build_us_intent) ─────────────────

def entry_price(side_ask: float, entry_price_max: float) -> float | None:
    """The live engine rule: one US tick inside the taker price for our side,
    clamped to the frozen entry cap and the hypothesis ceiling (0.90), rounded
    4dp.  Returns None when the clamped price falls outside the open interval
    (0, 1) — the engine would refuse to build such an order (price < US_TICK).
    """
    price = round(
        min(float(side_ask) - US_TICK, float(entry_price_max), ENTRY_CEILING), 4
    )
    # Engine-faithful validity (code-review f10a): build_us_intent refuses
    # price < US_TICK — a sub-tick price must not join the would-trade
    # population NOR consume the dedup key.
    return price if US_TICK <= price < 1.0 else None


def discount_pts(p_map: float, price: float) -> float:
    """Model-vs-entry discount in percentage points, 6dp-rounded (see module
    docstring: the rounding exists so the bucket boundaries are reproducible
    against binary float noise)."""
    return round((float(p_map) - float(price)) * 100.0, 6)


def discount_bucket(pts: float) -> str:
    """Bucket a discount in points: <5 lt5, [5,10) 5to10, [10,20) 10to20,
    >=20 gte20.  The low edge of each named bucket is inclusive, so exactly
    10.0 lands in "10to20".  Every real number lands in exactly one bucket
    (negative discounts are "lt5")."""
    if pts < 5.0:
        return "lt5"
    if pts < 10.0:
        return "5to10"
    if pts < 20.0:
        return "10to20"
    return "gte20"


def p_map_bucket(p_map: float) -> str:
    """Bucket p_map over the widened gate range [0.40, 0.90], half-open on the
    right of each named bucket (0.55 -> "0.55-0.70", 0.90 -> "0.80-0.90");
    anything below 0.40 or above 0.90 is "outside"."""
    if p_map < 0.40 or p_map > 0.90:
        return "outside"
    if p_map < 0.55:
        return "0.40-0.55"
    if p_map < 0.70:
        return "0.55-0.70"
    if p_map < 0.80:
        return "0.70-0.80"
    return "0.80-0.90"


def phase_bucket(round_number: int) -> str:
    """Map phase: <=5 early, <=15 mid, else late."""
    if round_number <= 5:
        return "early"
    if round_number <= 15:
        return "mid"
    return "late"


def band_bucket(p_map: float) -> str:
    """Engine band-gate classification: core 0.50<=p<=0.80, shoulder
    0.40<=p<=0.90, else outside."""
    if 0.50 <= p_map <= 0.80:
        return "core"
    if 0.40 <= p_map <= 0.90:
        return "shoulder"
    return "outside"


# ── input loading (pure: parse + count defects, never crash) ────────────────

def load_mapentry(path: str) -> tuple[list[dict], list[str]]:
    """Load mapentry JSONL rows, skipping blank lines and decode failures.

    Returns (rows, problems) where problems carries "path:lineno problem"
    strings — one per bad line, NOT printed to stderr: main() folds their count
    into the result JSON so the one-line stderr summary stays alone on stderr.
    '#' comment lines and bare '{#' annotation stubs pass through silently
    (same convention as wave_edge_join.py / mapentry2_score.py).
    """
    rows: list[dict] = []
    problems: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            code = line.lstrip()
            if code.startswith("#"):
                continue
            sline = line.strip()
            if not sline:
                continue
            if sline.startswith("{#"):  # jq-style annotated stub, ignore
                continue
            if len(line.encode("utf-8")) > MAX_LINE_BYTES:
                problems.append(f"{path}:{lineno} exceeds {MAX_LINE_BYTES} bytes")
                continue
            try:
                obj = json.loads(sline)
            except json.JSONDecodeError:
                problems.append(f"{path}:{lineno} JSON decode error")
                continue
            if isinstance(obj, dict):
                rows.append(obj)
            else:
                problems.append(f"{path}:{lineno} non-object line")
    return rows, problems


def load_winners(path: str) -> tuple[dict[str, dict], int]:
    """Load the winners JSON document written by scripts/grid_winners.py.

    Returns (winners_by_key, skipped) where skipped counts entries that are not
    objects.  Raises ValueError if the document has no "winners" dict — a
    structurally wrong file is caller error, not a skippable row.
    """
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    raw = doc.get("winners") if isinstance(doc, dict) else None
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a top-level object with a 'winners' dict")
    winners: dict[str, dict] = {}
    skipped = 0
    for k, v in raw.items():
        if isinstance(v, dict):
            winners[k] = v
        else:
            skipped += 1
    return winners, skipped


def load_fills(path: str) -> tuple[set[tuple[str, str]], int]:
    """Load the optional fills document: a JSON list of {"market_slug",
    "us_side", "order_id"} objects (a top-level {"fills": [...]} wrapper is also
    accepted, since some emitters wrap their lists).

    Returns ((market_slug, us_side) pairs, skipped) where skipped counts items
    that are not objects or lack a usable slug/side.  order_id is accepted but
    not needed for matching.
    """
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if isinstance(doc, dict) and isinstance(doc.get("fills"), list):
        items = doc["fills"]
    elif isinstance(doc, list):
        items = doc
    else:
        raise ValueError(f"{path}: expected a JSON list of fill objects")
    pairs: set[tuple[str, str]] = set()
    skipped = 0
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        slug = item.get("market_slug")
        side = item.get("us_side")
        if isinstance(slug, str) and slug and side in ("long", "short"):
            pairs.add((slug, side))
        else:
            skipped += 1
    return pairs, skipped


# ── pre-entry shock detection (int-ns math only) ────────────────────────────

def build_shock_sequences(
    rows: list[Any],
) -> tuple[dict[tuple, list[tuple[int, float]]], dict[int, tuple]]:
    """Group valid-p_map mapentry rows by (series_id, map_number, team).

    Returns (sequences, positions):
      sequences: key -> [(ts_ns, p_map), ...] sorted by ts_ns (file order breaks
        ties);
      positions: id(row) -> (key, index into its sequence), for O(1) lookup of
        where a candidate row sits in its own sequence.

    A row joins a sequence only when it has identity-safe series_id/map_number,
    a non-empty team string, an int (non-bool) ts_ns, and a unit-interval
    p_map — qualifying or not, since the model path is identical.  Rows missing
    any ingredient are silently absent: they can neither anchor a sequence nor
    be evaluated (they are counted elsewhere).
    """
    groups: dict[tuple, list[tuple[int, int, float, int]]] = {}
    for order, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        rtype = row.get("type")
        if rtype is not None and rtype != "mapentry":
            continue
        if not (_identity_element(row.get("series_id"))
                and _identity_element(row.get("map_number"))):
            continue
        team = row.get("team")
        if not isinstance(team, str) or not team:
            continue
        ts = row.get("ts_ns")
        if not isinstance(ts, int) or isinstance(ts, bool):
            continue
        p = _safe_float(row.get("p_map"))
        if p is None or not (0.0 <= p <= 1.0):
            continue
        key = (row["series_id"], row["map_number"], team)
        groups.setdefault(key, []).append((ts, order, p, id(row)))

    sequences: dict[tuple, list[tuple[int, float]]] = {}
    positions: dict[int, tuple] = {}
    for key, items in groups.items():
        items.sort(key=lambda t: (t[0], t[1]))
        sequences[key] = [(t[0], t[2]) for t in items]
        for idx, item in enumerate(items):
            positions[item[3]] = (key, idx)
    return sequences, positions


def has_pre_shock(
    seq: list[tuple[int, float]], idx: int, threshold: float, window_ns: int
) -> bool:
    """True iff some pair of consecutive rows in seq with BOTH rows inside the
    inclusive window [t - window_ns, t] (t = seq[idx][0]) has |delta p_map| >=
    threshold.

    Walks backward from the evaluation row: for j <= idx the pair
    (seq[j-1], seq[j]) has seq[j].ts <= t automatically (sorted), and once an
    earlier row falls before the window start every remaining pair does too —
    so the scan stops at the first out-of-window row.  The pair whose second
    element IS the evaluation row counts: that jump is realized strictly before
    the entry decision.  All ts arithmetic is int-ns.
    """
    t = seq[idx][0]
    start = t - window_ns
    for j in range(idx, 0, -1):
        a_ts, a_p = seq[j - 1]
        if a_ts < start:
            break
        # 6dp rounding before the boundary test (code-review f11): a true
        # 25pt jump like 0.70-0.45 is 0.24999999999999994 in binary — the
        # freeze evidence must not flip on which decimals capture emitted.
        if round(abs(seq[j][1] - a_p), 6) >= threshold:
            return True
    return False


# ── population selection (engine dedup mirror, pure) ────────────────────────

def select_candidates(
    rows: list[Any],
    shock_threshold: float = DEFAULT_SHOCK,
    shock_window_s: float = DEFAULT_SHOCK_WINDOW_S,
) -> tuple[list[dict], dict[str, int], dict[str, int]]:
    """Gate mapentry rows into the would-trade population.

    Returns (candidates, reasons, counters):
      candidates: one dict per would-trade evaluation, carrying every
        per-evaluation field except the settled outcome (won/edge are attached
        by settle_candidates);
      reasons: {malformed_row, entry_price_out_of_range, duplicate_key} counts;
      counters: {rows_loaded, out_of_scope_type, non_qualifying_rows}.

    Non-dict rows are the loader's problem list (already counted there) and are
    skipped here.  Gate order is deliberate: out-of-scope type -> non-qualifying
    -> well-formedness -> price validity -> dedup.  Dedup keys are consumed only
    by rows that pass price validity (a degenerate price never built an order),
    and dedup runs BEFORE verdict lookup (the engine dedupes at intent time).
    """
    window_ns = int(round(shock_window_s * NS_PER_S))
    sequences, positions = build_shock_sequences(rows)
    candidates: list[dict] = []
    reasons = {REASON_MALFORMED: 0, REASON_PRICE: 0, REASON_DUPLICATE: 0}
    counters = {"rows_loaded": 0, "out_of_scope_type": 0, "non_qualifying_rows": 0}
    seen: set = set()

    for row in rows:
        if not isinstance(row, dict):
            continue
        counters["rows_loaded"] += 1
        rtype = row.get("type")
        if rtype is not None and rtype != "mapentry":
            counters["out_of_scope_type"] += 1
            continue
        if row.get("qualifies") is not True:
            counters["non_qualifying_rows"] += 1
            continue

        series_id = row.get("series_id")
        map_number = row.get("map_number")
        team = row.get("team")
        ts_ns = row.get("ts_ns")
        round_number = row.get("round_number")
        slug = row.get("market_slug")
        us_side = row.get("us_side")
        # `in` on a tuple compares by equality (no hashing), so an unhashable
        # us_side cannot crash here.
        if not (_identity_element(series_id)
                and _identity_element(map_number)
                and isinstance(team, str) and team
                and isinstance(ts_ns, int) and not isinstance(ts_ns, bool)
                and isinstance(round_number, int)
                and not isinstance(round_number, bool)
                and isinstance(slug, str) and slug
                and us_side in ("long", "short")):
            reasons[REASON_MALFORMED] += 1
            continue
        p_map = _safe_float(row.get("p_map"))
        side_ask = _safe_float(row.get("side_ask"))
        entry_max = _safe_float(row.get("entry_price_max"))
        if (p_map is None or not _is_unit_interval(p_map)
                or side_ask is None or not _is_unit_interval(side_ask)
                or entry_max is None):
            reasons[REASON_MALFORMED] += 1
            continue

        price = entry_price(side_ask, entry_max)
        if price is None:
            reasons[REASON_PRICE] += 1
            continue
        key = (series_id, map_number, slug, us_side)
        if key in seen:
            reasons[REASON_DUPLICATE] += 1
            continue
        seen.add(key)

        pos = positions.get(id(row))
        if pos is None:
            # Unreachable for rows that passed the gates above (they satisfy
            # every sequence ingredient); defensive only, never a crash.
            shock = False
        else:
            gkey, idx = pos
            shock = has_pre_shock(sequences[gkey], idx, shock_threshold, window_ns)

        candidates.append({
            "series_id": series_id,
            "map_number": map_number,
            "team": team,
            "ts_ns": ts_ns,
            "round_number": round_number,
            "market_slug": slug,
            "us_side": us_side,
            "side": us_side,
            "p_map": p_map,
            "side_ask": side_ask,
            "entry_price_max": entry_max,
            "entry_price": price,
            "discount_pts": discount_pts(p_map, price),
            "shock_pre": shock,
        })
    return candidates, reasons, counters


def settle_candidates(
    candidates: list[dict], winners: dict[str, dict]
) -> tuple[list[dict], dict[str, int]]:
    """Attach settled outcomes from the winners file ONLY (never inferred).

    Returns (evals, reasons) where evals are the candidates plus "won" (1.0 /
    0.0, exact-string comparison against the verdict) and "edge"
    (won - entry_price, 6dp), and reasons counts {no_verdict,
    team_not_in_verdict}.  A team matching neither verdict slot is never
    guessed from scores or ordering.
    """
    reasons = {REASON_NO_VERDICT: 0, REASON_NOT_IN_VERDICT: 0}
    evals: list[dict] = []
    for cand in candidates:
        verdict = winners.get(f"{cand['series_id']}:{cand['map_number']}")
        if not isinstance(verdict, dict) or verdict.get("winner") is None:
            reasons[REASON_NO_VERDICT] += 1
            continue
        team = cand["team"]
        winner = verdict.get("winner")
        loser = verdict.get("loser")
        if team == winner:
            won = 1.0
        elif team == loser:
            won = 0.0
        else:
            reasons[REASON_NOT_IN_VERDICT] += 1
            continue
        evals.append({**cand, "won": won, "edge": round(won - cand["entry_price"], 6)})
    return evals, reasons


# ── metrics (pure) ──────────────────────────────────────────────────────────

def _metrics(bucket_rows: list[dict]) -> dict:
    """Aggregate one stratum bucket; empty buckets report n=0, null metrics."""
    n = len(bucket_rows)
    if n == 0:
        return {"n": 0, "win_rate": None, "mean_edge": None, "total_edge": None}
    total = sum(r["edge"] for r in bucket_rows)
    return {
        "n": n,
        "win_rate": round(sum(r["won"] for r in bucket_rows) / n, 6),
        "mean_edge": round(total / n, 6),
        "total_edge": round(total, 6),
    }


def _group_by(evals: list[dict], keyfn, order: tuple) -> dict:
    grouped: dict[str, list[dict]] = {k: [] for k in order}
    for r in evals:
        grouped[keyfn(r)].append(r)
    return {k: _metrics(grouped[k]) for k in order}


def compute_strata(evals: list[dict]) -> dict:
    """Compute every stratum over settled evaluations, with fixed key order."""
    disc: dict[str, list[dict]] = {b: [] for b in DISCOUNT_BUCKETS}
    phase: dict[str, list[dict]] = {b: [] for b in PHASE_BUCKETS}
    nested: dict[str, dict[str, list[dict]]] = {
        b: {p: [] for p in PHASE_BUCKETS} for b in DISCOUNT_BUCKETS
    }
    for r in evals:
        d = discount_bucket(r["discount_pts"])
        p = phase_bucket(r["round_number"])
        disc[d].append(r)
        phase[p].append(r)
        nested[d][p].append(r)
    return {
        "discount": {k: _metrics(v) for k, v in disc.items()},
        "p_map": _group_by(evals, lambda r: p_map_bucket(r["p_map"]), PMAP_BUCKETS),
        "phase": {k: _metrics(v) for k, v in phase.items()},
        "shock_pre": _group_by(
            evals, lambda r: "true" if r["shock_pre"] else "false", SHOCK_BUCKETS
        ),
        "side": _group_by(evals, lambda r: r["side"], SIDE_BUCKETS),
        "band": _group_by(evals, lambda r: band_bucket(r["p_map"]), BAND_BUCKETS),
        "discount_x_phase": {
            d: {p: _metrics(v) for p, v in buckets.items()}
            for d, buckets in nested.items()
        },
    }


# ── CLI ─────────────────────────────────────────────────────────────────────

def _validate_shock(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid float value: {text!r}")
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError(f"shock must be finite >= 0, got {text!r}")
    return value


def _validate_window_s(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid float value: {text!r}")
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(
            f"shock-window-s must be finite > 0, got {text!r}"
        )
    return value


def _fmt(x: float | None) -> str:
    return f"{x:.4f}" if x is not None else "null"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stratify settled would-trade mapentry entries by entry "
                    "quality (analysis only; evidence for pre-registered "
                    "policy amendments)."
    )
    parser.add_argument(
        "files", nargs="+",
        help="mapentry JSONL day file(s); order matters within and across files "
             "(first qualifying row per dedup key wins)",
    )
    parser.add_argument(
        "--winners", required=True,
        help="winners JSON file (output of grid_winners.py --out)",
    )
    parser.add_argument(
        "--fills",
        help="optional fills JSON: list of {market_slug, us_side, order_id} to "
             "tag the actual-fill subset",
    )
    parser.add_argument(
        "--shock", type=_validate_shock, default=DEFAULT_SHOCK,
        help=f"|delta p_map| threshold for a pre-entry shock "
             f"(default {DEFAULT_SHOCK})",
    )
    parser.add_argument(
        "--shock-window-s", type=_validate_window_s, dest="shock_window_s",
        default=DEFAULT_SHOCK_WINDOW_S,
        help=f"shock lookback window in seconds (default {DEFAULT_SHOCK_WINDOW_S})",
    )
    parser.add_argument(
        "--out", help="also write the JSON result to this file "
                      "(refuses to overwrite an existing file)"
    )
    args = parser.parse_args(argv)

    rows: list[dict] = []
    problems = 0
    for path in args.files:
        try:
            file_rows, file_problems = load_mapentry(path)
        except OSError as exc:
            print(f"entry_quality: cannot read {path}: {exc}", file=sys.stderr)
            return 1
        rows.extend(file_rows)
        problems += len(file_problems)

    try:
        winners, winners_skipped = load_winners(args.winners)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"entry_quality: cannot load {args.winners}: {exc}", file=sys.stderr)
        return 1

    fill_pairs: set[tuple[str, str]] | None = None
    fills_skipped: int | None = None
    if args.fills:
        try:
            fill_pairs, fills_skipped = load_fills(args.fills)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"entry_quality: cannot load {args.fills}: {exc}", file=sys.stderr)
            return 1

    candidates, cand_reasons, counters = select_candidates(
        rows, shock_threshold=args.shock, shock_window_s=args.shock_window_s
    )
    evals, settle_reasons = settle_candidates(candidates, winners)

    excluded = {reason: 0 for reason in EXCLUDED_REASONS}
    excluded[REASON_UNPARSEABLE] += problems
    for reason, count in cand_reasons.items():
        excluded[reason] += count
    for reason, count in settle_reasons.items():
        excluded[reason] += count

    strata = compute_strata(evals)
    fills_subset = None
    if fill_pairs is not None:
        tagged = [r for r in evals if (r["market_slug"], r["us_side"]) in fill_pairs]
        fills_subset = {"n": len(tagged), "strata": compute_strata(tagged)}

    result = {
        "n": len(evals),
        "excluded": excluded,
        "strata": strata,
        "fills_subset": fills_subset,
        "params": {
            "live_us_band_gate": list(US_BAND_GATE),
            "files": list(args.files),
            "winners": args.winners,
            "fills": args.fills,
            "shock": args.shock,
            "shock_window_s": args.shock_window_s,
            "rows_loaded": counters["rows_loaded"],
            "out_of_scope_type": counters["out_of_scope_type"],
            "non_qualifying_rows": counters["non_qualifying_rows"],
            "winners_skipped": winners_skipped,
            "fills_loaded_pairs": len(fill_pairs) if fill_pairs is not None else None,
            "fills_skipped": fills_skipped,
        },
    }
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)

    if args.out:
        try:
            with open(args.out, "x", encoding="utf-8") as fh:
                fh.write(text)
        except FileExistsError:
            # Refuse to clobber prior analysis artifacts (corpus safety).
            print(f"entry_quality: --out file already exists: {args.out}",
                  file=sys.stderr)
            return 1
        except OSError as exc:
            print(f"entry_quality: cannot write {args.out}: {exc}", file=sys.stderr)
            return 1

    nonzero = {k: v for k, v in excluded.items() if v}
    excl_total = sum(excluded.values())
    excl_txt = f"excluded={excl_total}"
    if nonzero:
        excl_txt += " [" + ",".join(f"{k}={v}" for k, v in nonzero.items()) + "]"
    disc = strata["discount"]
    parts = [
        f"settled={len(evals)}",
        excl_txt,
        " ".join(f"{b}:n={disc[b]['n']}" for b in DISCOUNT_BUCKETS),
        f"shock true={strata['shock_pre']['true']['n']}/"
        f"false={strata['shock_pre']['false']['n']}",
    ]
    if fill_pairs is not None and fills_subset is not None:
        parts.append(f"fills_tagged={fills_subset['n']}")
    print("entry_quality: " + " | ".join(parts), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
