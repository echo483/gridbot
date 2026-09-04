#!/usr/bin/env python3
"""
postshock_lag.py — After a violent model repricing ("shock"), does the book
lead or lag the model, and how long until they re-converge?

Analysis only (slow loop, iron rail 1): reads existing mapentry JSONL day
files, detects model shocks, and measures how the book-vs-model gap
(side_ask - p_map) evolves over the window that follows.  MEASUREMENT ONLY:
this registers no hypothesis, recommends nothing, and its output makes no
trading claim — it describes observed book/model lag, nothing more.
Nothing here touches orders, keys, or the network, and it never modifies
any data.

Motivating observation (not a recommendation): Overtake-NuTorious map2 flip
2026-08-27 — p_map dropped ~0.92 -> 0.40 while the book asked 0.83-0.92, i.e.
the book sat far ABOVE the model for a stretch.  H-WAVE1 tuning found the
opposite sign on upward wave starts.

Shock definition (bracketed constants are CLI flags):
  Rows are grouped into sequences by (input file, series_id, map_number,
  team) and ordered by ts_ns (stable for ties).  Scoping by input file is
  deliberate: day files abut multi-hour capture holes, and letting a
  match's tail in one file meet its head in the next would turn a slow
  repricing across dead air into a fabricated instant shock.  Only rows
  with numeric p_map
  participate in sequencing; a SHOCK fires at row t when
      |p_map(t) - p_map(prev)| >= [--shock]
  where prev is the previous valid row of the sequence.  The sign of the
  change gives direction: "up" or "down".  At most one ACTIVE measurement
  window per sequence at a time: a new shock arriving while a window is open
  RESTARTS the window — the open window is closed early (flagged
  ended_by_restart), the restart is counted, and the new shock opens a
  fresh window.

Measurement per shock, over the window t .. t + [--window-s] seconds
(inclusive):
  Each row inside the window carrying BOTH numeric p_map and numeric
  side_ask yields gap(ts) = side_ask - p_map.  Per shock we report:
    gap_at_shock      first in-window book gap (the shock row itself
                      qualifies as "at/after shock"),
    gap_at_end        last in-window book gap,
    time_to_converge  seconds from the shock to the FIRST in-window row
                      with |gap| <= [--converge] (null if never within
                      the window),
    n_book_obs        number of in-window book observations,
    direction         "up" | "down",
    ended_by_restart  this window was cut short by a later shock.
  Rows without book data are excluded from gap metrics but the sequence and
  its active window stay alive across them.

Aggregate caveats the JSON encodes honestly:
  gap_at_shock sign separates regimes: >0 = book ABOVE model at the shock
  (NuTorious-style lag-down), <0 = book BELOW model (wave-start lag-up).
  The pooled median_gap_at_shock mixes those regimes by construction, so
  it must always be read beside the *_above_model / *_below_model splits
  and by_direction.  converge_rate is null (not 0.0) whenever no shock
  carried any book observation: convergence is undecidable there, not
  failed.

Usage:
    uv run python scripts/postshock_lag.py <mapentry.jsonl> [...] \
        [--shock 0.25] [--window-s 300] [--converge 0.05] [--out FILE]

Output: JSON to stdout (mirrored to --out if given); one-line summary to
stderr.  Malformed rows are skipped and counted, never fatal.  All timestamp
arithmetic is exact integer nanoseconds; seconds appear only as derived,
rounded outputs.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from statistics import median
from typing import Any

# ── constants ──────────────────────────────────────────────────────────────

DEFAULT_SHOCK = 0.25
DEFAULT_WINDOW_S = 300
DEFAULT_CONVERGE = 0.05

NS_PER_S = 1_000_000_000

# At most this many stderr warnings per category during load; the rest are
# counted in stats["warnings_suppressed"] so a corrupted batch write cannot
# flood the one-line-summary channel.
MAX_LOAD_WARNINGS = 5

# Private per-row provenance tag (index into load_data's file list).  It
# partitions sequences per input file; untagged rows (direct detect()
# callers) share one sequencing universe.
SOURCE_FILE_TAG = "_postshock_source_index"

# ── data loading ───────────────────────────────────────────────────────────


def load_data(file_paths: list[str]) -> tuple[list[dict], dict]:
    """Load mapentry JSONL rows from one or more files.

    Returns (rows, stats).  Malformed lines and non-object JSON values are
    skipped (first MAX_LOAD_WARNINGS get a stderr warning, all are counted
    in `stats`); unreadable files raise OSError so the CLI can fail loudly.

    Every loaded row is tagged with SOURCE_FILE_TAG = its position in
    file_paths.  detect() partitions sequences by that tag, so no sequence
    ever spans a boundary between input files — concatenated day files are
    separated by capture holes whose true p_map deltas are unknowable, and
    bridging them would fabricate shocks.  Duplicate paths in file_paths
    would silently double-count rows into otherwise-identical sequences,
    so they are called out on stderr and tallied.
    """
    # Reviewer F6: cap stderr warnings so the mandated one-line summary
    # channel survives a corrupted batch write; overflow lands in counts.
    warned = 0

    rows: list[dict] = []
    stats: dict[str, Any] = {
        "lines_read": 0,
        "lines_blank": 0,
        "lines_malformed_json": 0,
        "lines_non_object": 0,
        "warnings_suppressed": 0,
    }
    n_files = len(file_paths)
    seen_paths: set[str] = set()
    dup_path_indexes: list[int] = []
    for idx, path in enumerate(file_paths):
        # realpath (not just normpath) so relative spellings of the same
        # file collapse onto their absolute form.
        norm = os.path.realpath(path)
        if norm in seen_paths:
            dup_path_indexes.append(idx)
            seen_idx = next(
                i
                for i, p in enumerate(file_paths[:idx])
                if os.path.realpath(p) == norm
            )
            print(
                f"postshock_lag warning: duplicate input {path!r} "
                f"(arg {idx + 1}; already loaded as arg {seen_idx + 1}) — "
                f"its rows will double-count inside their own file's "
                f"sequences",
                file=sys.stderr,
            )
        else:
            seen_paths.add(norm)

        with open(path) as f:
            for lineno, line in enumerate(f, 1):
                stats["lines_read"] += 1
                line = line.strip()
                if not line:
                    stats["lines_blank"] += 1
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as e:
                    stats["lines_malformed_json"] += 1
                    if warned < MAX_LOAD_WARNINGS:
                        warned += 1
                        print(
                            f"warning: {path}:{lineno} JSON decode error: "
                            f"{e}",
                            file=sys.stderr,
                        )
                    else:
                        stats["warnings_suppressed"] += 1
                    continue
                if not isinstance(obj, dict):
                    stats["lines_non_object"] += 1
                    if warned < MAX_LOAD_WARNINGS:
                        warned += 1
                        print(
                            f"warning: {path}:{lineno} not a JSON object; "
                            f"skipped",
                            file=sys.stderr,
                        )
                    else:
                        stats["warnings_suppressed"] += 1
                    continue
                # Provenance scoping (reviewer F1): a row belongs to its
                # own file's sequencing universe only.
                obj[SOURCE_FILE_TAG] = idx
                rows.append(obj)
    stats["n_input_files"] = n_files
    stats["n_duplicate_inputs"] = len(dup_path_indexes)
    return rows, stats


# ── value guards ───────────────────────────────────────────────────────────


def _safe_float(v: Any) -> float | None:
    """Coerce a numeric field to float; None on missing/bool/NaN/inf/bad."""
    if v is None or isinstance(v, bool):
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    return fv if math.isfinite(fv) else None


def _safe_ts_ns(v: Any) -> int | None:
    """Accept only true integer nanosecond timestamps (no float ts math)."""
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    return v


def _norm_key_part(v: Any) -> Any:
    """Make a sequence-key part hashable without mangling normal scalars.

    Reviewer F7 (applied, partial): tuple/set/frozenset parts are
    canonicalized like dict/list; a NaN part can never equal itself under
    hash-then-identity lookup, so it is pinned to one fixed stand-in — NaN
    team names would otherwise split (or, via CPython's cached bare-NaN
    singleton, silently merge) sequences depending on the loader.
    Bool-vs-int identity collisions (True == 1) are inherent Python hashing;
    they are noted here rather than contorted away.
    """
    if isinstance(v, bool):  # before int-ish checks; hashable as-is
        return v
    if isinstance(v, float) and math.isnan(v):
        return ("<nan>",)
    if isinstance(v, (dict, list, tuple, set, frozenset)):
        try:
            return json.dumps(v, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return repr(v)
    return v


def _seq_key(r: dict) -> tuple[Any, ...]:
    """Sequence identity: (source file, series_id, map_number, team).

    The provenance tag partitions per input file (reviewer F1); untagged
    rows share one universe.
    """
    tag = r.get(SOURCE_FILE_TAG) if isinstance(r, dict) else None
    return (
        _norm_key_part(tag),
        _norm_key_part(r.get("series_id")),
        _norm_key_part(r.get("map_number")),
        _norm_key_part(r.get("team")),
    )


# ── shock/measurement engine ───────────────────────────────────────────────


def _new_record(src: dict, key: tuple[Any, ...]) -> dict:
    _src_tag, series_id, map_number, team = key
    return {
        "series_id": series_id,
        "map_number": map_number,
        "team": team,
        "market_slug": src.get("market_slug"),
        "us_side": src.get("us_side"),
        "round_number": src.get("round_number"),
        "direction": None,
        "shock_ts_ns": None,
        "window_start_ns": None,
        "window_end_ns": None,
        "p_map": None,
        "prev_p_map": None,
        "delta": None,
        "gap_at_shock": None,
        "gap_at_end": None,
        "time_to_converge": None,
        "n_book_obs": 0,
        "ended_by_restart": False,
    }


def _close_window(
    window: dict,
    *,
    ended_by_restart: bool,
    converge: float,
) -> None:
    """Finalize a shock's measurement window into its record."""
    rec = window["rec"]
    obs = window["obs"]  # [(ts_ns, gap)] in chronological order
    rec["ended_by_restart"] = ended_by_restart
    rec["n_book_obs"] = len(obs)
    if obs:
        rec["gap_at_shock"] = round(obs[0][1], 6)
        rec["gap_at_end"] = round(obs[-1][1], 6)
    for ts, gap in obs:
        if abs(gap) <= converge:
            rec["time_to_converge"] = round(
                (ts - window["anchor_ns"]) / NS_PER_S, 6
            )
            break


def detect(
    rows: list[dict],
    shock: float = DEFAULT_SHOCK,
    window_s: int = DEFAULT_WINDOW_S,
    converge: float = DEFAULT_CONVERGE,
) -> dict:
    """Run the post-shock lag measurement over loaded mapentry rows."""
    # Reviewer F4: reject NaN outright -- every comparison below would be
    # False, so shock=nan silently detects nothing and converge=nan makes
    # convergence impossible while emitting non-strict JSON.
    for name, val in (("shock", shock), ("converge", converge)):
        if not math.isfinite(val):
            raise ValueError(f"--{name} must be finite")
    if shock <= 0:
        raise ValueError("--shock must be > 0")
    if window_s <= 0:
        raise ValueError("--window-s must be >= 1")
    if converge < 0:
        raise ValueError("--converge must be >= 0")

    window_ns = int(window_s) * NS_PER_S

    # Group into sequences, keeping only rows with a usable integer ts_ns.
    seqs: dict[tuple[Any, ...], list[dict]] = defaultdict(list)
    n_input = 0
    n_no_ts = 0
    source_tags: set[Any] = set()
    n_untagged = 0
    for r in rows:
        n_input += 1
        if not isinstance(r, dict):
            n_no_ts += 1
            continue
        ts = _safe_ts_ns(r.get("ts_ns"))
        if ts is None:
            n_no_ts += 1
            continue
        key = _seq_key(r)
        tag_part = key[0]
        if tag_part is None:
            n_untagged += 1  # direct callers bypassing load_data
        else:
            source_tags.add(tag_part)
        seqs[key].append(
            {
                "ts": ts,
                "p": _safe_float(r.get("p_map")),
                "ask": _safe_float(r.get("side_ask")),
                "src": r,
            }
        )

    for seq in seqs.values():
        seq.sort(key=lambda item: item["ts"])  # stable: ties keep file order

    shocks: list[dict] = []
    n_restarts = 0

    for key, seq in seqs.items():
        prev_p: float | None = None
        active: dict | None = None  # open measurement window, at most one

        for item in seq:
            ts = item["ts"]

            # Expire a finished window before touching this row.
            if active is not None and ts > active["end_ns"]:
                _close_window(active, ended_by_restart=False, converge=converge)
                active = None

            # Shock detection (valid-p_map rows only).
            if item["p"] is not None:
                if prev_p is not None and abs(item["p"] - prev_p) >= shock:
                    delta = item["p"] - prev_p
                    # New shock during an open window restarts the window.
                    if active is not None:
                        _close_window(
                            active, ended_by_restart=True, converge=converge
                        )
                        active = None
                        n_restarts += 1
                    rec = _new_record(item["src"], key)
                    rec["direction"] = "up" if delta >= 0 else "down"
                    rec["shock_ts_ns"] = ts
                    rec["window_start_ns"] = ts
                    rec["window_end_ns"] = ts + window_ns
                    rec["p_map"] = round(item["p"], 6)
                    rec["prev_p_map"] = round(prev_p, 6)
                    rec["delta"] = round(delta, 6)
                    shocks.append(rec)
                    active = {
                        "anchor_ns": ts,
                        "end_ns": ts + window_ns,
                        "rec": rec,
                        "obs": [],
                    }
                prev_p = item["p"]

            # Feed this row's book quote to the active window, if any.  The
            # shock row itself is the first "at/after shock" observation;
            # rows lacking either price contribute no gap but don't disturb
            # the window.
            if (
                active is not None
                and item["p"] is not None
                and item["ask"] is not None
                and ts <= active["end_ns"]
            ):
                active["obs"].append((ts, item["ask"] - item["p"]))

        if active is not None:
            _close_window(active, ended_by_restart=False, converge=converge)

    # Stable, input-order-independent ordering across sequences (reviewer
    # F3): numeric-aware total order on key parts -- ints/floats compare
    # numerically (map 2 < map 10), strings sort beside other strings,
    # mixed types rank before them so no comparison is ever attempted.
    # Same-type ties are resolved deterministically via repr.
    def _sort_part(v: Any) -> tuple[int, Any]:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return 1, repr(v)
        return 0, v

    shocks.sort(
        key=lambda rec: (
            _sort_part(rec["series_id"]),
            _sort_part(rec["map_number"]),
            _sort_part(rec["team"]),
            rec["shock_ts_ns"],
        )
    )

    counts = {
        "n_rows_input": n_input,
        "n_rows_without_valid_ts": n_no_ts,
        "n_sequences": len(seqs),
        # Reviewer F1: provenance echoes.  source_files counts distinct
        # input files that contributed sequenced rows; untagged_rows marks
        # direct detect() callers that bypassed load_data's tagging.
        "n_source_files": len(source_tags),
        "n_untagged_rows": n_untagged,
    }

    return {
        "params": {"shock": shock, "window_s": window_s, "converge": converge},
        "counts": counts,
        "shocks": shocks,
        "summary": _summarize(shocks, n_restarts),
    }


# ── aggregation ────────────────────────────────────────────────────────────


def _bucket(shocks: list[dict]) -> dict:
    """Aggregate stats for one subset of shock records (never crashes)."""
    n = len(shocks)
    n_with_book = sum(1 for rec in shocks if rec["n_book_obs"] > 0)
    n_converged = sum(
        1 for rec in shocks if rec["time_to_converge"] is not None
    )
    ttcs = [
        rec["time_to_converge"]
        for rec in shocks
        if rec["time_to_converge"] is not None
    ]
    gaps_at_shock = [
        rec["gap_at_shock"] for rec in shocks if rec["gap_at_shock"] is not None
    ]
    # Reviewer F2: sign splits.  gap>0 means the book sat ABOVE the model
    # at the shock (lag-down regime, NuTorious-style); gap<0 means BELOW
    # (wave-start regime).  The pooled median mixes these regimes by
    # construction and must be read beside these counts.
    gaps_above = [g for g in gaps_at_shock if g > 0]
    gaps_below = [g for g in gaps_at_shock if g < 0]
    return {
        "n_shocks": n,
        "n_window_restarts": sum(
            1 for rec in shocks if rec["ended_by_restart"]
        ),
        "n_with_book": n_with_book,
        "n_converged": n_converged,
        # Reviewer F5: with zero book observations convergence is
        # UNDECIDABLE, not failed -- report null like every other
        # empty-set aggregate instead of a fake 0.0.
        "converge_rate": (
            round(n_converged / n_with_book, 6) if n_with_book else None
        ),
        "median_time_to_converge_s": median(ttcs) if ttcs else None,
        "median_gap_at_shock": median(gaps_at_shock) if gaps_at_shock else None,
        "gap_sign_split_at_shock": {
            "n_book_above_model": len(gaps_above),
            "n_book_below_model": len(gaps_below),
            "median_gap_when_above": (
                round(median(gaps_above), 6) if gaps_above else None
            ),
            "median_gap_when_below": (
                round(median(gaps_below), 6) if gaps_below else None
            ),
        },
    }


def _summarize(shocks: list[dict], n_restarts: int) -> dict:
    summary = _bucket(shocks)
    # Reviewer F6: this invariant previously lived in an assert, which
    # evaporates under python -O; make it an explicit hard failure so a
    # future refactor cannot silently desync the tallies.
    if summary["n_window_restarts"] != n_restarts:
        raise RuntimeError(
            f"internal invariant violated: restart tally mismatch "
            f"(records={summary['n_window_restarts']} "
            f"counter={n_restarts})"
        )
    by_direction = {}
    for d in ("up", "down"):
        by_direction[d] = _bucket([r for r in shocks if r["direction"] == d])
    summary["by_direction"] = by_direction
    return summary


# ── CLI entry point ────────────────────────────────────────────────────────


def _fmt(v: float | None) -> str:
    return "null" if v is None else f"{v:.4f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Post-shock book-vs-model lag measurement over mapentry JSONL "
            "(analysis only; no trading implications)."
        ),
    )
    parser.add_argument("files", nargs="+", help="mapentry JSONL file(s)")
    parser.add_argument(
        "--shock", type=float, default=DEFAULT_SHOCK,
        help=f"|p_map step| that qualifies as a shock (default {DEFAULT_SHOCK})",
    )
    parser.add_argument(
        "--window-s", type=int, default=DEFAULT_WINDOW_S,
        help=f"measurement window length in seconds (default {DEFAULT_WINDOW_S})",
    )
    parser.add_argument(
        "--converge", type=float, default=DEFAULT_CONVERGE,
        help=f"|gap| considered converged (default {DEFAULT_CONVERGE})",
    )
    parser.add_argument(
        "--out", help="also write the JSON result to this file",
    )
    args = parser.parse_args(argv)

    try:
        rows, load_stats = load_data(args.files)
    except OSError as exc:
        print(f"postshock_lag: cannot read input: {exc}", file=sys.stderr)
        return 1

    try:
        result = detect(
            rows,
            shock=args.shock,
            window_s=args.window_s,
            converge=args.converge,
        )
    except ValueError as exc:
        print(f"postshock_lag: {exc}", file=sys.stderr)
        return 2

    result["counts"].update(load_stats)

    # Reviewer F4: allow_nan=False makes any non-finite leak a loud error
    # instead of emitting bare NaN/Infinity tokens strict parsers reject.
    text = json.dumps(result, indent=2, allow_nan=False)
    print(text)
    if args.out:
        try:
            with open(args.out, "w") as f:
                f.write(text + "\n")
        except OSError as exc:
            print(f"postshock_lag: cannot write {args.out}: {exc}",
                  file=sys.stderr)
            return 1

    s = result["summary"]
    split = s["gap_sign_split_at_shock"]
    counts = result["counts"]
    dup_note = (
        f" dup_inputs={counts['n_duplicate_inputs']}"
        if counts["n_duplicate_inputs"]
        else ""
    )
    suppressed = counts.get("warnings_suppressed", 0)
    warn_note = f" warnings_suppressed={suppressed}" if suppressed else ""
    # Reviewer F2: lead with the per-direction medians.  The pooled median
    # mixes opposite-sign regimes (book-above vs book-below) and is kept
    # only as a secondary echo beside its sign split.
    print(
        f"postshock: shocks={s['n_shocks']} with_book={s['n_with_book']} "
        f"restarts={s['n_window_restarts']} "
        f"gap_at_shock: up={_fmt(s['by_direction']['up']['median_gap_at_shock'])} "
        f"down={_fmt(s['by_direction']['down']['median_gap_at_shock'])} "
        f"(above={split['n_book_above_model']} "
        f"below={split['n_book_below_model']}) "
        f"pooled={_fmt(s['median_gap_at_shock'])} "
        f"ttc_median_s={_fmt(s['median_time_to_converge_s'])} "
        f"rate={_fmt(s['converge_rate'])} "
        f"sequences={counts['n_sequences']} "
        f"files={counts['n_source_files']} lines={counts['lines_read']}"
        f"{dup_note}{warn_note}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
