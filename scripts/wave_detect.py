#!/usr/bin/env python3
"""
wave_detect.py — Retrospective detector for the H-WAVE1 draft trigger.

Analysis only (slow loop, iron rail 1): reads existing mapentry JSONL rows and
reports which (series_id, map_number, market_slug, us_side) sequences fired a
"wave" — a first crossing where P_map enters [0.50, 0.80] from below with a
rise >= `min_rise` over the trailing `trail` distinct round_numbers.  Nothing
here touches orders, keys, or the network, and it never modifies any data.

Trigger (H-WAVE1 draft; bracketed constants are CLI flags):
  for each sequence ordered by ts_ns, a WAVE fires at the FIRST row where
    (a) 0.50 <= p_map <= 0.80,
    (b) the previous valid row's p_map < 0.50, and
    (c) p_map - min(p_map over the trailing `trail` distinct round_numbers
        before this row) >= `min_rise`.
  At most one wave per sequence.

Usage:
    uv run python scripts/wave_detect.py <mapentry.jsonl> [...] \
        [--min-rise 0.15] [--trail 3]

Output: JSON to stdout; a one-line summary to stderr.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from statistics import median
from typing import Any

# ── constants ──────────────────────────────────────────────────────────────

DEFAULT_MIN_RISE = 0.15
DEFAULT_TRAIL = 3

# ── data loading ───────────────────────────────────────────────────────────

def load_data(file_paths: list[str]) -> list[dict]:
    """Load mapentry JSONL rows from one or more files."""
    rows: list[dict] = []
    for path in file_paths:
        with open(path) as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(
                        f"warning: {path}:{lineno} JSON decode error: {e}",
                        file=sys.stderr,
                    )
    return rows


def _safe_float(v: Any) -> float | None:
    """Coerce a numeric field to float, returning None on missing/bad values."""
    if v is None:
        return None
    try:
        fv = float(v)
        if math.isfinite(fv):
            return fv
    except (ValueError, TypeError):
        pass
    return None


# ── trigger logic ──────────────────────────────────────────────────────────

def _trailing_min(prior_rows: list[dict], trail: int) -> float | None:
    """Minimum p_map over the last `trail` distinct round_numbers in prior_rows.

    `prior_rows` is the ordered list (by ts_ns) of valid-p_map rows that occur
    strictly before the current row.  Returns None when there are no prior rows.
    """
    rounds: list[Any] = []
    min_by_round: dict[Any, float] = {}
    for r in prior_rows:
        rn = r.get("round_number")
        p = r["p_map"]
        if rn in min_by_round:
            min_by_round[rn] = min(min_by_round[rn], p)
        else:
            rounds.append(rn)
            min_by_round[rn] = p
    if not rounds:
        return None
    trail_rounds = rounds[-trail:] if len(rounds) > trail else rounds
    return min(min_by_round[rn] for rn in trail_rounds)


def _scan_sequence(
    seq: list[dict],
    key: tuple[Any, Any, str, str],
    min_rise: float,
    trail: int,
) -> dict | None:
    """Return the single wave dict for one sequence, or None if it never fires."""
    series_id, map_number, market_slug, us_side = key

    # Rows with a valid p_map, in ts_ns order (input file order preserved by
    # the caller's sort; only valid p_map rows participate in the trigger).
    valid: list[dict] = []
    for r in seq:
        p = _safe_float(r.get("p_map"))
        if p is None:
            continue
        r = dict(r)
        r["p_map"] = p
        valid.append(r)

    prev_p: float | None = None
    for i, r in enumerate(valid):
        p = r["p_map"]

        # (a) band gate
        if not (0.50 <= p <= 0.80):
            prev_p = p
            continue

        # (b) must enter from below 0.50
        if prev_p is None or prev_p >= 0.50:
            prev_p = p
            continue

        # first crossing into band from below — apply (c) rise test
        tmin = _trailing_min(valid[:i], trail)
        if tmin is None:
            prev_p = p
            continue
        rise = p - tmin
        if rise >= min_rise:
            side_ask = _safe_float(r.get("side_ask"))
            return {
                "series_id": series_id,
                "map_number": map_number,
                "us_side": us_side,
                "market_slug": market_slug,
                "round_number": r.get("round_number"),
                "p_map": p,
                "prev_p_map": prev_p,
                "rise": round(rise, 6),
                "book_ask": side_ask,
                "book_vs_model": round(side_ask - p, 6) if side_ask is not None else None,
                "ts_ns": r.get("ts_ns"),
            }
        # rise insufficient → this crossing is consumed; no second chance
        prev_p = p

    return None


def detect(
    rows: list[dict],
    min_rise: float = DEFAULT_MIN_RISE,
    trail: int = DEFAULT_TRAIL,
) -> dict:
    """Run the H-WAVE1 retrospective detector over loaded mapentry rows."""
    if trail < 1:
        raise ValueError("--trail must be >= 1")

    # Group rows into sequences keyed by (series_id, map_number, market_slug,
    # us_side); order each by ts_ns.
    seqs: dict[tuple[Any, Any, str, str], list[dict]] = defaultdict(list)
    for r in rows:
        key = (
            r.get("series_id"),
            r.get("map_number"),
            str(r.get("market_slug", "")),
            str(r.get("us_side", "")),
        )
        seqs[key].append(r)

    for seq in seqs.values():
        seq.sort(key=lambda r: _safe_float(r.get("ts_ns")) if _safe_float(r.get("ts_ns")) is not None else 0)

    n_sequences = len(seqs)
    waves: list[dict] = []
    for key, seq in seqs.items():
        wave = _scan_sequence(seq, key, min_rise, trail)
        if wave is not None:
            waves.append(wave)

    n_waves = len(waves)
    wave_rate = (n_waves / n_sequences) if n_sequences else 0.0

    _bvm = [w["book_vs_model"] for w in waves if w["book_vs_model"] is not None]
    _median_bvm = median(_bvm) if _bvm else None

    # stable ordering for reproducibility
    waves.sort(key=lambda w: (str(w["series_id"]), w["map_number"], str(w["us_side"]), w["ts_ns"]))

    return {
        "n_sequences": n_sequences,
        "n_waves": n_waves,
        "waves": waves,
        "wave_rate": wave_rate,
        "params": {"min_rise": min_rise, "trail": trail},
    }


# ── CLI entry point ────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Retrospective H-WAVE1 wave detector over mapentry JSONL."
    )
    parser.add_argument("files", nargs="+", help="mapentry JSONL file(s)")
    parser.add_argument(
        "--min-rise", type=float, default=DEFAULT_MIN_RISE,
        help=f"minimum p_map rise to qualify as a wave (default {DEFAULT_MIN_RISE})",
    )
    parser.add_argument(
        "--trail", type=int, default=DEFAULT_TRAIL,
        help=f"trailing distinct round_numbers to measure the rise over (default {DEFAULT_TRAIL})",
    )
    args = parser.parse_args()

    rows = load_data(args.files)
    result = detect(rows, min_rise=args.min_rise, trail=args.trail)

    print(json.dumps(result, indent=2))

    bvm = [w["book_vs_model"] for w in result["waves"] if w["book_vs_model"] is not None]
    med_bvm = median(bvm) if bvm else None
    print(
        f"waves={result['n_waves']} sequences={result['n_sequences']} "
        f"rate={result['wave_rate']:.4f} median_book_vs_model={med_bvm}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
