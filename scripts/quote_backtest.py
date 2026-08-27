#!/usr/bin/env python3
"""
quote_backtest.py — A/B two maker-quote pricing rules on captured mapentry data.

Analysis only (slow loop, iron rail 1): reads H-MAPENTRY1 mapentry JSONL day
files and a settled-winners JSON (grid_winners.py --out shape), replays two
quote pricing rules for a BUY of the team-wins position, and reports would-fill
rates and settled edge.  Numbers only — NO recommendation lives here; any
entry-rule amendment is a separate future pre-registration in
docs/experiments/registry.jsonl.  No network, no orders, no keys, stdlib only.

RULES UNDER TEST (per evaluation row; every quote snapped to the venue's
US_TICK=0.01 grid at 4 decimal places, mirroring executor intents.build_us_intent):

  (a) CURRENT rule:      quote_a = round(min(side_ask - 0.01,
                                             entry_price_max,
                                             entry_ceiling(hyp)), 4)
  (b) INSIDE-SPREAD rule: quote_b = round(max(side_bid + 0.01,
                                             min(entry_price_max,
                                                 side_ask - 0.01)), 4)

- ``side_ask`` / ``bid`` / ``ask`` are the row's recorded fields.  NOTE THE
  PRICE FRAME (mapentry.py:159-163): ``bid``/``ask`` describe the LONG token's
  book while ``side_ask`` is frame-converted to OUR side ("long" -> ask,
  "short" -> 1 - bid).  Rule b therefore consumes ``side_bid``, OUR side's
  best bid: long -> ``bid``, short -> round(1 - ask, 4).  Using raw ``bid``
  for a short row mixes our asks with counterparty bids (adversarial review
  blocker 1: quotes up to 13x the cap, 54% of the population corrupted).
- ``entry_ceiling(hyp)`` (default 0.90, verdict.py ENTRY_ASK parity) replicates
  intents.py:169 exactly, making rule a the real CURRENT baseline.  In practice
  the third clamp rarely binds (H-MODELENTRY1 sits at the 0.90 default, and
  qualifying rows carry side_ask <= entry_price_max, so the cap arm usually
  degenerates to side_ask - 0.01 — reported honestly as a caveat, not hidden).
- A rule is SKIPPED on a row when any input is missing/non-numeric or its
  quote falls outside the open interval (0, 1); skips are counted per rule.

WOULD-FILL MODEL (approximation): a resting BUY at price P placed at ts_ns T is
"filled" iff ANY later mapentry row with the SAME market_slug AND same us_side,
T < ts' <= T + TTL ns, shows side_ask <= P (the book's ask travelled down
to/through our level).  This is OPTIMISTIC — a touched level is not a
guaranteed maker fill (queue ahead of us, post-only repricing, partial fills)
— and it is applied EQUALLY to both rules on overlapping populations, so the
A/B comparison is fair on rows both rules quote (n_common below).  Absolute
fill rates are upper bounds.  Both caveats ship in the JSON under "caveats".

POPULATION AND DEDUP: rows with qualifies == true AND numeric side_ask AND a
non-empty string market_slug/us_side/team and identity-safe
(series_id, map_number, int ts_ns).  ONE evaluation per
(series_id, map_number, market_slug, us_side) — the FIRST qualifying row in
CHRONOLOGICAL ts_ns order (ties broken by input line number), NOT argv/file
order: the engine sees one global event stream, so passing day files in a
different argv order must not change which row wins a dedup key (adversarial
review major 5).  Later qualifying rows on a consumed key count as
dedup_collisions and never become evaluations; unusable rows do not consume
their key (a corrupt row could not have traded).  Every parseable mapentry
row — qualifying or not — feeds the would-fill book path.

SETTLEMENT: verdicts come ONLY from the winners file at key
f"{series_id}:{map_number}" — a winner is never inferred from scores or team
order.  won = 1.0 if winner == team, 0.0 if loser == team (exact strings);
missing key or team in neither -> the evaluation is EXCLUDED from edge
metrics (counted by reason) but still counts for would-fill rates (a fill
needs no verdict).

BANDS: per-band metrics split by the row's p_map — "core" = p_map in
[0.50, 0.80] inclusive (the pre-registration band), "shoulder" = valid p_map
outside it; rows with non-numeric p_map land in neither band (counted
"band_unknown") but stay in the all-population metrics.  Band membership for
METRICS is independent of the live placement gate, which widened to
(0.40, 0.90) (intents.US_BAND_GATE); n_gated reports how many evaluations the
live engine would refuse to place.

Usage:
    uv run python scripts/quote_backtest.py MAPENTRY.jsonl [...] \
        --winners W.json [--ttl-s 150] [--out FILE]

Output: JSON to stdout ({per_rule, n_evaluations, excluded, caveats, params});
a ONE-LINE summary to stderr.  Malformed mapentry lines are skipped + counted
(params.mapentry_line_problems), never fatal.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
from typing import Any

__all__ = [
    "US_TICK",
    "DEFAULT_TTL_S",
    "CORE_BAND",
    "LIVE_BAND_GATE",
    "DEFAULT_ENTRY_CEILING",
    "ENTRY_ASK_CEILINGS",
    "RULE_IDS",
    "load_mapentry",
    "load_winners",
    "side_bid",
    "quote_a",
    "quote_b",
    "quote_for_rule",
    "band_label",
    "build_evaluations",
    "build_path_index",
    "attach_verdicts",
    "evaluate_fills",
    "rule_metrics",
    "compute_per_rule",
    "validate_ttl_s",
    "main",
]

US_TICK = 0.01                 # one venue tick (mirrors executor intents.US_TICK)
TICK_DP = 4                    # venue-tick decimal places (intents.py rounds quotes to 4dp)
DEFAULT_TTL_S = 150.0          # resting-order TTL in seconds
CORE_BAND = (0.50, 0.80)       # pre-registration core band; shoulders outside
LIVE_BAND_GATE = (0.40, 0.90)  # intents.US_BAND_GATE (temporary test widening)
DEFAULT_ENTRY_CEILING = 0.90   # intents.DEFAULT_ENTRY_CEILING
# verdict.ENTRY_ASK ceilings (prefix match, kept local — stdlib analysis lane).
ENTRY_ASK_CEILINGS = {"H-MANADV1": 0.85, "H-ECONBUY1": 0.78, "H-PISTOLCARRY1": 0.80}
RULE_IDS = ("a", "b")

# One-line formula strings, carried into params for artifact self-containment.
RULE_FORMULAS = {
    "a": "round(min(side_ask - 0.01, entry_price_max, entry_ceiling(hyp)), 4)",
    "b": "round(max(side_bid + 0.01, min(entry_price_max, side_ask - 0.01)), 4)"
         "  # side_bid = bid if us_side=='long' else round(1-ask, 4)",
}

CAVEATS = (
    "Would-fill model is OPTIMISTIC: a later row whose side_ask touches our "
    "level is not a guaranteed maker fill (queue ahead, post-only repricing, "
    "partial fills); fill rates are upper bounds.",
    "The same touch model is applied to both rules, so the A/B comparison of "
    "fill rates is fair; absolute levels are upper bounds either way.",
    "Rules may quote DIFFERENT row subsets because skip conditions differ "
    "(rule b needs our-side bid too); per-rule denominators are each rule's "
    "own n_quoted, so cross-rule comparisons should use n_common (rows where "
    "BOTH rules quote) rather than raw headlines.",
    "Settled edge uses the rule's quote as the fill price; real execution "
    "(fees, slippage, partial size) is not modelled.",
    "Verdicts come only from the winners file; a winner is never inferred "
    "from scores or team order.",
    "The cap arm of rule a rarely binds: qualifying rows already satisfy "
    "side_ask <= entry_price_max (mapentry.attach_book), so quote_a degenerates "
    "to side_ask - 0.01 on most of the population — the INSIDE-SPREAD contrast "
    "is the informative axis, not the cap.",
    "Metrics cover the recording population, not the live gate: n_gated rows "
    "sit outside the live (0.40, 0.90) US_BAND_GATE and the engine would not "
    "place them today.",
)

# Maximum bytes scanned per mapentry line before it is treated as corrupt
# (mirrors wave_edge_join: real capture rows are ~500 bytes).
MAX_LINE_BYTES = 1024 * 1024

# Half-tick safety net for the fill probe (adversarial review blocker 2:
# binary-float subtraction lands ~1e-17 below true tick levels).  Quotes are
# rounded onto the 0.01 grid first; the epsilon only guards residual error.
EPS_PROBE = US_TICK / 2


# ── helpers ────────────────────────────────────────────────────────────────

def _safe_float(v: Any) -> float | None:
    """Coerce a numeric field to float, returning None on missing/bad values.

    Accepts ints, floats, and decimal strings; rejects booleans and
    non-finite values.  OverflowError is caught because JSON integers are
    unbounded: float(10**400) raises OverflowError rather than ValueError,
    and one such field must never crash the run (hard rule: handle malformed
    rows defensively, as wave_edge_join does).
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


def _identity_element(v: Any) -> bool:
    """Strict type gate for one identity element (str/int, bools excluded).

    Python unifies hash/equality across int, float, and bool (100 == 100.0 ==
    True): without this gate an upstream schema drift could silently weld two
    different dedup keys together.  Same contract as wave_edge_join.
    """
    return isinstance(v, (str, int)) and not isinstance(v, bool)


def _is_int(v: Any) -> bool:
    """True iff v is a genuine int (bools excluded) — ts_ns gate."""
    return isinstance(v, int) and not isinstance(v, bool)


def _r6(x: float | None) -> float | None:
    """Round a metric for output; None passes through (never round(None))."""
    return None if x is None else round(x, 6)


def _fmt(x: float | None) -> str:
    """Format a metric for the one-line stderr summary."""
    return f"{x:.4f}" if x is not None else "null"


def _usd(value: float) -> float:
    """Snap a price onto the venue's 0.01-tick grid at 4dp (intents parity).

    Adversarial-review blocker 2: ``0.29 - 0.01`` in binary float is
    0.27999999999999997, so an unrounded quote silently rejects a book that
    prints exactly 0.28.  All quote prices pass through here; the residual
    half-tick epsilon in the probe is the belt to these braces.
    """
    return round(value, TICK_DP)


def _ceil_for(hypothesis_id: str) -> float:
    """entry_ceiling(hyp) parity with intents.entry_ceiling (prefix match)."""
    hyp = hypothesis_id if isinstance(hypothesis_id, str) else ""
    for prefix, ceiling in ENTRY_ASK_CEILINGS.items():
        if hyp.startswith(prefix):
            return ceiling
    return DEFAULT_ENTRY_CEILING


# ── input loading (parse + count defects, never crash on row defects) ──────

def load_mapentry(path: str) -> tuple[list[dict], list[str]]:
    """Load mapentry JSONL rows, skipping blank/comment/decode-bad lines.

    Returns (rows, problems) where problems carries "path:lineno problem"
    strings — one per bad line, NOT printed to stderr: main() folds the count
    into params so the one-line stderr contract holds.  '#' prefixes and bare
    '{#' markers pass through silently (same convention as wave_edge_join).
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

    Returns (winners_by_key, skipped) where skipped counts entries that are
    not objects.  Raises ValueError if the document has no "winners" dict —
    a structurally wrong file is caller error, not a skippable row.
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


# ── price frame + quote rules (pure) ───────────────────────────────────────

def side_bid(us_side: Any, bid: Any, ask: Any) -> float | None:
    """OUR side's best bid, in the SAME frame as row['side_ask'].

    mapentry.side_ask() frame-converts the effective ask ("long" -> the long
    token's ask, "short" -> 1 - bid); the matching bid conversion is
    long -> bid, short -> round(1 - ask, 4).  Feeding a short row's raw
    ``bid`` (the counterparty's price!) to a quote rule prices our level in
    the wrong frame entirely.  Returns None on missing/invalid inputs or an
    unrecognised us_side (such rows simply cannot define rule b's floor).
    """
    fbid, fask = _safe_float(bid), _safe_float(ask)
    if us_side == "long":
        return fbid
    if us_side == "short":
        return None if fask is None else _usd(1.0 - fask)
    return None


def quote_a(side_ask: float | None, entry_price_max: float | None,
            entry_ceiling: float = DEFAULT_ENTRY_CEILING) -> float | None:
    """CURRENT rule, engine-faithful (intents.build_us_intent): the three-arm
    min() clamped price, tick-snapped to 4dp.

    None when inputs are missing/non-numeric or the quote is not in (0, 1).
    """
    parts = (side_ask, entry_price_max, entry_ceiling)
    if any(p is None for p in parts):
        return None
    # Type narrowing: every arm is a float here (the guard above rejected
    # None), so the three-arm min() is well-typed.
    sa, cap, ceiling = float(side_ask), float(entry_price_max), float(entry_ceiling)
    q = _usd(min(sa - US_TICK, cap, ceiling))
    return q if 0.0 < q < 1.0 else None


def quote_b(side_ask: float | None, entry_price_max: float | None,
            side_bid_v: float | None) -> float | None:
    """INSIDE-SPREAD rule: max(our_bid + tick, min(cap, side_ask - tick)).

    Literal Queue-13 brief formula applied verbatim (including the verbatim
    case where our_bid + tick exceeds the min() term on a crossed/thin book —
    the registry, not this tool, judges the rule), tick-snapped.  None on
    missing/non-numeric inputs (our-side bid included) or a quote outside (0,1).
    """
    if side_ask is None or entry_price_max is None or side_bid_v is None:
        return None
    q = _usd(max(side_bid_v + US_TICK,
                 min(entry_price_max, side_ask - US_TICK)))
    return q if 0.0 < q < 1.0 else None


def quote_for_rule(rule_id: str, side_ask: float | None,
                   entry_price_max: float | None,
                   side_bid_v: float | None = None,
                   entry_ceiling: float = DEFAULT_ENTRY_CEILING) -> float | None:
    """Single dispatch used by BOTH quoting and fill replay (no drift)."""
    if rule_id == "a":
        return quote_a(side_ask, entry_price_max, entry_ceiling)
    if rule_id == "b":
        return quote_b(side_ask, entry_price_max, side_bid_v)
    return None


def band_label(p_map: float | None) -> str | None:
    """Band for a row's p_map: "core" in [0.50, 0.80] inclusive, "shoulder"
    for any OTHER FINITE value; None when p_map is missing/non-numeric (NaN
    is not a valid shoulder — it is garbage, routed out of both bands)."""
    if p_map is None or not math.isfinite(p_map):
        return None
    return "core" if CORE_BAND[0] <= p_map <= CORE_BAND[1] else "shoulder"


def live_band_gated(p_map: float | None) -> bool:
    """True when the LIVE engine would refuse to place this row (intents
    us_band_gated parity at (0.40, 0.90)); missing p_map fails closed."""
    if p_map is None or not math.isfinite(p_map):
        return True
    return not (LIVE_BAND_GATE[0] <= p_map <= LIVE_BAND_GATE[1])


# ── population + dedup (pure: fresh eval dicts, defects counted) ───────────

def build_evaluations(rows: list[Any]) -> tuple[list[dict], dict[str, int]]:
    """Build one evaluation per (series_id, map_number, market_slug, us_side)
    from the FIRST qualifying row in CHRONOLOGICAL ts_ns order.

    Ordering (adversarial review major 5): candidates are ordered by their
    float-coercible ts_ns BEFORE the dedup loop, ties broken by original
    input index — otherwise passing day-2 before day-1 in argv hands the
    dedup key to whichever file happens to concatenate first.  Unparseable
    timestamps cannot win an ordering race at all (gated below anyway).

    Population gates, in order: qualifies is exactly True; identity-safe
    series_id/map_number; non-empty string market_slug/us_side/team; a
    genuine int ts_ns that coerces to float; numeric side_ask.  A row failing
    any gate is counted "unusable_population_rows" and does NOT consume its
    dedup key (a corrupt row could not have traded); every fully-gated row
    consumes its key.  Later gated rows on a consumed key count as
    "dedup_collisions" — they never become evaluations (engine semantics).

    Each evaluation carries its rule quotes (None = skipped for that rule),
    its hypothesis ceiling, band, and live-gate flag.  Returns
    (evaluations_chronological, counts).
    """
    counts = {
        "qualifying_rows_seen": 0,
        "dedup_collisions": 0,
        "unusable_population_rows": 0,
    }

    def usable(rw: dict) -> tuple[bool, float]:
        """Gate one candidate; (ok, ts_as_float_or_inf)."""
        if rw.get("qualifies") is not True:
            return False, math.inf
        sid = rw.get("series_id")
        mn = rw.get("map_number")
        slug = rw.get("market_slug")
        us = rw.get("us_side")
        ts = rw.get("ts_ns")
        team = rw.get("team")
        if not (_identity_element(sid) and _identity_element(mn)
                and isinstance(slug, str) and slug
                and isinstance(us, str) and us
                and isinstance(team, str) and team
                and _is_int(ts)):
            # Unhashable containers (list/dict slug), floats/bools in identity
            # fields, non-int ts, missing names — all route here, never raise.
            return False, math.inf
        ts_f = _safe_float(ts)
        if ts_f is None:
            # A genuine int that overflows float() is data corruption; it
            # also cannot sit on a TTL timeline safely.
            return False, math.inf
        if _safe_float(rw.get("side_ask")) is None:
            return False, math.inf
        return True, ts_f

    # Chronological ordering pass (stable: preserves file/index order on ties).
    indexed = [(idx, rw) for idx, rw in enumerate(rows) if isinstance(rw, dict)]
    ordered = sorted(indexed, key=lambda p: p[0])          # placeholder: stable base
    ordered = sorted(indexed, key=lambda p: (usable(p[1])[1], p[0]))

    seen: set[tuple[Any, Any, str, str]] = set()
    evals: list[dict] = []
    for _, rw in ordered:
        if not isinstance(rw, dict):
            continue
        rtype = rw.get("type")  # untyped legacy rows still count as mapentry
        if rtype is not None and rtype != "mapentry":
            continue
        ok, ts_f = usable(rw)
        if not ok:
            if rw.get("qualifies") is True:
                # It claimed to qualify but failed a gate: count it so the
                # defect surface stays visible in params/excluded.
                counts["unusable_population_rows"] += 1
            continue

        counts["qualifying_rows_seen"] += 1
        sid = rw["series_id"]
        mn = rw["map_number"]
        slug = rw["market_slug"]
        us = rw["us_side"]
        ts = rw["ts_ns"]
        key = (sid, mn, slug, us)
        if key in seen:
            counts["dedup_collisions"] += 1
            continue
        seen.add(key)

        eff = _safe_float(rw.get("side_ask"))
        cap = _safe_float(rw.get("entry_price_max"))
        sbid = side_bid(us, rw.get("bid"), rw.get("ask"))
        p_map = _safe_float(rw.get("p_map"))
        hyp = rw.get("hypothesis_id")
        hyp_str = hyp if isinstance(hyp, str) else ""

        evals.append({
            "series_id": sid,
            "map_number": mn,
            "market_slug": slug,
            "us_side": us,
            "team": rw["team"],
            "ts_ns": ts,
            "p_map": p_map,
            "band": band_label(p_map),
            "gated_live": live_band_gated(p_map),
            "hypothesis_id": hyp_str,
            "side_ask": eff,
            "side_bid": sbid,
            "entry_price_max": cap,
            "quotes": {
                "a": quote_for_rule("a", eff, cap, sbid, _ceil_for(hyp_str)),
                "b": quote_for_rule("b", eff, cap, sbid),
            },
            "fills": {"a": False, "b": False},
            "won": None,
        })

    counts["band_unknown"] = sum(1 for e in evals if e["band"] is None)
    return evals, counts


def build_path_index(rows: list[Any]) -> dict[tuple[str, str], tuple[list, list]]:
    """Index every parseable mapentry row by (market_slug, us_side) for the
    would-fill book path: time-sorted (ts_list, side_ask_list) per key.

    Deliberately ignores qualifies — ANY later row on the same slug/side shows
    the book state, qualifying or not.  Rows with non-numeric side_ask/ts or
    non-string slug/us_side contribute nothing (skipped, not fatal).
    """
    raw: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        rtype = row.get("type")
        if rtype is not None and rtype != "mapentry":
            continue
        slug = row.get("market_slug")
        us = row.get("us_side")
        if not isinstance(slug, str) or not slug:
            continue
        if not isinstance(us, str) or not us:
            continue
        ts = _safe_float(row.get("ts_ns"))
        eff = _safe_float(row.get("side_ask"))
        if ts is None or eff is None:
            continue
        raw.setdefault((slug, us), []).append((ts, eff))
    index: dict[tuple[str, str], tuple[list, list]] = {}
    for k, pairs in raw.items():
        pairs.sort(key=lambda p: p[0])
        index[k] = ([p[0] for p in pairs], [p[1] for p in pairs])
    return index


def _touches(path_entry: tuple[list, list] | None, t0: float, ttl_ns: float,
             price: float) -> bool:
    """Would-fill probe: any later row on this book path with
    T < ts' <= T + ttl showing side_ask <= price (+half-tick epsilon).

    Strict lower bound (an evaluation's own row at ts' == T can never fill
    itself), inclusive upper bound (a touch at exactly T + TTL counts).  The
    epsilon is blocker-2 insurance UNDER the 4dp tick-snapped quotes; a raw
    book print at the quote's snapped level compares clean.
    """
    if path_entry is None:
        return False
    ts_list, ask_list = path_entry
    lo = bisect.bisect_right(ts_list, t0)
    hi = bisect.bisect_right(ts_list, t0 + ttl_ns)
    limit = price + EPS_PROBE
    return any(a <= limit for a in ask_list[lo:hi])


def evaluate_fills(evals: list[dict],
                   path: dict[tuple[str, str], tuple[list, list]],
                   ttl_ns: float) -> None:
    """Fill in each evaluation's per-rule would-fill booleans, in place.

    Quotes are recomputed here via the SAME quote_for_rule dispatch used to
    seed evaluations — one pricing source, no drift (adversarial review minor
    8).  OPTIMISTIC model (module docstring); rules without a quote stay
    unfilled.
    """
    for e in evals:
        entry = path.get((e["market_slug"], e["us_side"]))
        for rule in RULE_IDS:
            q = quote_for_rule(rule, e["side_ask"], e["entry_price_max"],
                               e["side_bid"], _ceil_for(e["hypothesis_id"]))
            e["fills"][rule] = (
                q is not None and _touches(entry, float(e["ts_ns"]), ttl_ns, q)
            )


# ── settlement (verdict file ONLY; never inferred) ─────────────────────────

def attach_verdicts(evals: list[dict], winners: dict[str, dict]) -> dict[str, int]:
    """Attach settled won (1.0/0.0) to each evaluation, in place.

    Lookup key f"{series_id}:{map_number}" in the winners file.  Missing key
    (or a verdict without a string winner) -> "no_verdict_key"; team matching
    NEITHER winner nor loser (exact strings) -> "team_in_neither" — a winner
    is never inferred from scores or team order.  Excluded evaluations keep
    won=None: still counted for fill rates, excluded from edge metrics.
    Returns counts by reason (both keys always present).
    """
    counts = {"no_verdict_key": 0, "team_in_neither": 0}
    for e in evals:
        verdict = winners.get(f"{e['series_id']}:{e['map_number']}")
        if not isinstance(verdict, dict) or not isinstance(verdict.get("winner"), str):
            counts["no_verdict_key"] += 1
            continue
        winner = verdict["winner"]
        loser = verdict.get("loser")
        if e["team"] == winner:
            e["won"] = 1.0
        elif isinstance(loser, str) and e["team"] == loser:
            e["won"] = 0.0
        else:
            counts["team_in_neither"] += 1
    return counts


# ── metrics (pure) ─────────────────────────────────────────────────────────

def _wilson_lo(k: int, n: int) -> float | None:
    """Wilson-score LOWER bound for a binomial proportion (z=1.96).

    Repo discipline (sizing/kelly q_eff, verdict analyzer) prefers Wilson
    bounds over naked rates; the decision metric edge_x_fillrate inherits the
    pessimistic bound of its two factors rather than a bare point estimate.
    None-passthrough mirrors empty populations.
    """
    if n <= 0:
        return None
    z = 1.959963984540054
    phat = k / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))
    return (centre - margin) / denom


def rule_metrics(evals: list[dict], rule_id: str,
                 n_common: int | None = None) -> dict:
    """Metrics for one rule over a set of evaluations (no nested bands).

    fill_rate's denominator is THIS rule's n_quoted — rules can quote
    different subsets (skip conditions differ), so cross-rule readouts should
    prefer n_common (supplied when known).  n_gated counts evaluations outside
    the live (0.40, 0.90) band gate among ALL rows in scope.  Empty
    populations -> null metrics, never division by zero.
    """
    quoted = [e for e in evals if e["quotes"][rule_id] is not None]
    n_quoted = len(quoted)
    n_skipped = len(evals) - n_quoted
    filled = [e for e in quoted if e["fills"][rule_id]]
    would_fill = len(filled)
    fill_rate = (would_fill / n_quoted) if n_quoted else None
    settled = [e for e in filled if e["won"] is not None]
    n_settled_fills = len(settled)
    edge = (sum(e["won"] - e["quotes"][rule_id] for e in settled)
            / n_settled_fills) if settled else None
    mean_quote = (sum(e["quotes"][rule_id] for e in quoted) / n_quoted
                  if n_quoted else None)
    exf = (edge * fill_rate) if (edge is not None and fill_rate is not None) \
        else None
    # Decision-number uncertainty (minor 9): fill leg via Wilson lower bound;
    # edge leg untouched (edge CI machinery is out of scope for n<=20 cells).
    wilson_fill = _wilson_lo(would_fill, n_quoted)
    exf_wilson = (edge * wilson_fill) \
        if (edge is not None and wilson_fill is not None) else None
    n_gated = sum(1 for e in evals if e["gated_live"])
    out = {
        "n_quoted": n_quoted,
        "n_skipped": n_skipped,
        "n_common": n_common,
        "would_fill": would_fill,
        "fill_rate": _r6(fill_rate),
        "fill_rate_wilson_lb": _r6(wilson_fill),
        "n_settled_fills": n_settled_fills,
        "settled_edge_per_fill": _r6(edge),
        "mean_quote": _r6(mean_quote),
        "edge_x_fillrate": _r6(exf),
        "edge_x_fillrate_wilson_lb": _r6(exf_wilson),
        "n_gated": n_gated,
    }
    return out


def compute_per_rule(evals: list[dict]) -> dict[str, dict]:
    """Headline metrics per rule over ALL evaluations, plus per-band splits
    nested under "core" / "shoulder" (band membership from each row's p_map;
    band_unknown rows live only in the headline)."""
    common = sum(1 for e in evals
                 if e["quotes"]["a"] is not None and e["quotes"]["b"] is not None)
    out: dict[str, dict] = {}
    for rule in RULE_IDS:
        m = rule_metrics(evals, rule, n_common=common)
        m["core"] = rule_metrics(
            [e for e in evals if e["band"] == "core"], rule)
        m["shoulder"] = rule_metrics(
            [e for e in evals if e["band"] == "shoulder"], rule)
        out[rule] = m
    return out


# ── CLI ────────────────────────────────────────────────────────────────────

def validate_ttl_s(text: str) -> float:
    """Parse --ttl-s, rejecting non-finite/non-positive values with a clean
    argparse-style exit (nan/inf would poison the JSON output)."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid float value: {text!r}")
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(
            f"ttl-s must be finite and > 0, got {text!r}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="A/B two maker-quote pricing rules on captured mapentry "
                    "data: would-fill rate and settled edge per rule "
                    "(numbers only; the registry decides later)."
    )
    parser.add_argument("mapentry", nargs="+",
                        help="mapentry JSONL day file(s) (repeatable, "
                             "any order — dedup uses ts_ns chronology)")
    parser.add_argument("--winners", required=True,
                        help="winners JSON file (output of grid_winners.py)")
    parser.add_argument("--ttl-s", type=validate_ttl_s, default=DEFAULT_TTL_S,
                        help=f"resting-order TTL in seconds "
                             f"(default {DEFAULT_TTL_S:g})")
    parser.add_argument("--out", help="also write the JSON result to this file "
                                      "(refuses to overwrite an existing file)")
    args = parser.parse_args(argv)

    try:
        rows: list[dict] = []
        problems: list[str] = []
        for path in args.mapentry:
            file_rows, file_problems = load_mapentry(path)
            rows.extend(file_rows)
            problems.extend(file_problems)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"quote_backtest: cannot load mapentry inputs: {exc}",
              file=sys.stderr)
        return 1
    try:
        winners, skipped_winners = load_winners(args.winners)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"quote_backtest: cannot load {args.winners}: {exc}",
              file=sys.stderr)
        return 1

    ttl_ns = args.ttl_s * 1e9
    path_index = build_path_index(rows)
    evals, counts = build_evaluations(rows)
    attach_counts = attach_verdicts(evals, winners)
    evaluate_fills(evals, path_index, ttl_ns)
    per_rule = compute_per_rule(evals)

    excluded = {
        "no_verdict_key": attach_counts["no_verdict_key"],
        "team_in_neither": attach_counts["team_in_neither"],
        "unusable_population_rows": counts["unusable_population_rows"],
        "band_unknown": counts["band_unknown"],
    }

    result = {
        "per_rule": per_rule,
        "n_evaluations": len(evals),
        "excluded": excluded,
        "caveats": list(CAVEATS),
        "params": {
            "ttl_s": args.ttl_s,
            "ttl_ns": ttl_ns,
            "tick": US_TICK,
            "tick_dp": TICK_DP,
            "core_band": list(CORE_BAND),
            "live_band_gate": list(LIVE_BAND_GATE),
            "rules": dict(RULE_FORMULAS),
            "frame_note": "row['bid']/row['ask'] are LONG-token book tops; "
                          "side_ask/side_bid are converted to OUR us_side "
                          "frame (mapentry.side_ask parity)",
            "entry_ceiling_default": DEFAULT_ENTRY_CEILING,
            "entry_ceilings_by_prefix": dict(ENTRY_ASK_CEILINGS),
            "mapentry_files": list(args.mapentry),
            "mapentry_rows_loaded": len(rows),
            "mapentry_line_problems": len(problems),
            "winners_file": args.winners,
            "winners_loaded": len(winners),
            "winners_skipped": skipped_winners,
            "qualifying_rows_seen": counts["qualifying_rows_seen"],
            "dedup_collisions": counts["dedup_collisions"],
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
            print(f"quote_backtest: --out file already exists: {args.out}",
                  file=sys.stderr)
            return 1
        except OSError as exc:
            print(f"quote_backtest: cannot write {args.out}: {exc}",
                  file=sys.stderr)
            return 1

    parts = [f"evals={len(evals)} common={per_rule['a']['n_common']}"]
    for rule in RULE_IDS:
        m = per_rule[rule]
        parts.append(
            f"{rule}: quoted={m['n_quoted']} fill={_fmt(m['fill_rate'])} "
            f"edge={_fmt(m['settled_edge_per_fill'])} "
            f"exf={_fmt(m['edge_x_fillrate'])}"
        )
    for band in ("core", "shoulder"):
        parts.append(
            f"{band}: a_exf={_fmt(per_rule['a'][band]['edge_x_fillrate'])} "
            f"b_exf={_fmt(per_rule['b'][band]['edge_x_fillrate'])}"
        )
    print(f"quote_backtest: " + " | ".join(parts), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
