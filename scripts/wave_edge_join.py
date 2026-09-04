#!/usr/bin/env python3
"""
wave_edge_join.py — Join H-WAVE1 waves to settled map winners and measure edge.

Analysis only (slow loop, iron rail 1): joins detected "waves" (model-probability
surges, from scripts/wave_detect.py) with the settled map-winner oracle
(scripts/grid_winners.py) to quantify whether the wave trigger had real edge.
Nothing here touches orders, keys, or the network, and it never modifies data.

Pipeline per wave:
  1. team     — waves carry no team name; recover it from the mapentry JSONL by
                matching (series_id, map_number, ts_ns) EXACTLY.  "Exactly" is a
                strict contract here: each identity element must be a str or int
                (bools excluded), so Python's hash unification of
                int == float == True cannot silently weld e.g. map_number=1 and
                map_number=1.0 together.  First matching row wins; no match ->
                excluded with reason "no_team_match".
  2. verdict  — looked up in the winners file at key f"{series_id}:{map_number}".
                Missing key -> "no_verdict".  The verdict comes ONLY from that
                file: a winner is never inferred from scores or team order.
  3. won      — 1.0 if verdict["winner"] == team else 0.0, compared as exact
                strings.  If team matches NEITHER winner nor loser ->
                "team_not_in_verdict".

Wave-level sanity ranges (a wave failing any of these is excluded as
"malformed_wave" rather than allowed to poison the headline metric):
p_map, rise in [0, 1] (rise bounds are a cheap corruption tripwire; the tool's
job is measuring realized outcomes, not policing the detector's output range);
book_ask, when present, in [0, 1] because it enters settled_book_edge as the
price paid per $1 of settlement — an out-of-range ask makes that quantity
definitionally meaningless for go/no-go decisions under pre-registration
discipline.  Out-of-range values are data corruption until proven otherwise,
so they take the same path as non-numeric garbage instead of flowing silently
into averages.

Joined waves are bucketed by rise: "lo" = rise < --rise-split, "hi" =
rise >= --rise-split, plus "all".  Per bucket: n, win_rate, mean_p_map,
calib_gap (win_rate - mean_p_map), brier, n_book, and over the n_book subset
(those with a numeric executable book_ask):
  mean_book_vs_model = mean(book_ask - p_map)
  settled_book_edge  = mean(won - book_ask)   <- headline: realized $ edge per
                                                 $1 stake of buying the wave side
                                                 at the book ask at wave time.

Usage:
    uv run python scripts/wave_edge_join.py WAVES.json MAPENTRY.jsonl \
        WINNERS.json [--rise-split 0.30] [--out FILE]

Output: JSON to stdout ({buckets, rows, excluded, params}); a ONE-LINE summary
to stderr.  Corrupt mapentry lines are reported as {"event": "warning"} rows
inside the JSON, not as extra stderr lines — the stderr contract stays exactly
one summary line regardless of how many defects each input contains.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Any

__all__ = [
    "DEFAULT_RISE_SPLIT",
    "bucket_label",
    "build_team_index",
    "compute_buckets",
    "join_waves",
    "load_mapentry",
    "load_waves",
    "load_winners",
    "main",
]

DEFAULT_RISE_SPLIT = 0.30

# Reason codes for excluded waves (single source of truth for callers/tests).
REASON_MALFORMED = "malformed_wave"
REASON_NO_TEAM = "no_team_match"
REASON_NO_VERDICT = "no_verdict"
REASON_NOT_IN_VERDICT = "team_not_in_verdict"

BUCKET_ORDER = ("lo", "hi", "all")

# Maximum bytes scanned per mapentry line before it is treated as corrupt.
# Guards against pathological unbounded lines in hand-mangled files; real
# capture rows are ~500 bytes.
MAX_LINE_BYTES = 1024 * 1024


# ── helpers ────────────────────────────────────────────────────────────────

def _safe_float(v: Any) -> float | None:
    """Coerce a numeric field to float, returning None on missing/bad values.

    Accepts ints, floats, and decimal strings (e.g. "0.55"); rejects booleans,
    non-finite values, and coercion failures.  OverflowError is caught because
    JSON integers are unbounded: float(10**400) raises OverflowError rather
    than ValueError, and one such field must never crash the run (hard rule:
    handle malformed rows defensively).

    Deliberately more defensive than scripts.wave_detect._safe_float, which
    does NOT reject booleans there (float(True) == 1.0 upstream).  This file
    intentionally refuses to treat a bool as a probability or price.  The
    producer-side book_ask values this tool reads were coerced by the upstream
    version; any leak through it dies here.
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
    True, identical hashes).  Without this gate an upstream schema drift from
    int ts_ns to float ts_ns would SILENTLY match — welding together two
    different data points, which defeats the spec's "ints/strs as-is" intent.
    Bools are rejected because True == 1 would alias map_number/ts_ns 1.
    Strings stay strings: no int<->str coercion either way.
    """
    return isinstance(v, (str, int)) and not isinstance(v, bool)


def _has_identity_key(row: Any) -> bool:
    """True iff every element of the (series_id, map_number, ts_ns) triple has
    an identity-safe type."""
    return (
        isinstance(row, dict)
        and _identity_element(row.get("series_id"))
        and _identity_element(row.get("map_number"))
        and _identity_element(row.get("ts_ns"))
    )


# ── input loading (pure: parse + count defects, never crash) ───────────────

def load_waves(path: str) -> tuple[list[dict], int]:
    """Load the waves JSON document written by scripts/wave_detect.py.

    Returns (waves, skipped) where skipped counts list entries that are not
    objects.  Raises ValueError if the document has no "waves" list at all —
    a structurally wrong file is caller's error, not a skippable row.
    """
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    raw = doc.get("waves") if isinstance(doc, dict) else None
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a top-level object with a 'waves' list")
    waves: list[dict] = []
    skipped = 0
    for item in raw:
        if isinstance(item, dict):
            waves.append(item)
        else:
            skipped += 1
    return waves, skipped


def load_mapentry(path: str) -> tuple[list[dict], list[str]]:
    """Load mapentry JSONL rows, skipping blank lines and decode failures.

    Returns (rows, problems) where problems carries "path:lineno problem"
    strings — one per bad line, NOT printed to stderr: main() folds them into
    the result JSON as warning events so the one-line stderr summary stays
    alone on stderr.  Defects are skipped + counted, never fatal.
    """
    rows: list[dict] = []
    problems: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            # Comment convention, mirroring mapentry2_score.py's loader: '#'
            # prefixes and bare '{#' markers pass through silently so capture
            # archives can carry annotations without polluting the counts.
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
    not objects.  Raises ValueError if the document has no "winners" dict.
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


def build_team_index(rows: list[dict]) -> dict[tuple[Any, Any, Any], str]:
    """Index mapentry rows by exact (series_id, map_number, ts_ns) -> first team.

    Every identity element must pass the _identity_element gate (str/int, no
    bools, no floats): rows keyed on hash-unifiable types like ts_ns=100.0 are
    IGNORED rather than indexed, so they can neither shadow nor be reached by
    an int-keyed lookup.  Duplicate identities keep the FIRST row seen.  Rows
    typed as another record kind, or lacking a non-empty "team" string, are
    ignored too.
    """
    index: dict[tuple[Any, Any, Any], str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        rtype = row.get("type")  # untyped legacy rows still count as mapentry
        if rtype is not None and rtype != "mapentry":
            continue
        team = row.get("team")
        if not isinstance(team, str) or not team:
            continue
        if not _has_identity_key(row):  # also skips dict/list/unhashable parts
            continue
        index.setdefault(
            (row["series_id"], row["map_number"], row["ts_ns"]), team
        )
    return index


# ── join (pure) ────────────────────────────────────────────────────────────

def join_waves(
    waves: list[Any],
    team_index: dict[tuple[Any, Any, Any], str],
    winners: dict[str, dict],
) -> tuple[list[dict], list[dict]]:
    """Join waves to recovered teams and settled verdicts.

    Returns (rows, excluded), both ordered deterministically by
    (series_id, map_number, ts_ns).

    Joined rows carry: series_id, map_number, team, ts_ns, rise, p_map,
    book_ask, won, overtime.  Excluded entries carry a "reason" code
    (malformed_wave | no_team_match | no_verdict | team_not_in_verdict) plus
    whatever identifiers the wave had; team_not_in_verdict entries also carry
    the recovered "team" and the verdict's winner/loser pair, which is the
    diagnostic a research operator needs to spot GRID-vs-Polymarket name drift.

    Malformed waves are dropped into "excluded", never raised: anything that
    could make downstream code blow up (non-dict shapes, missing/uncoercible
    numerics, out-of-range probabilities, unhashable container values, giant
    integers that overflow float()) is filtered at this boundary.
    """

    def _exclude(reason: str, wave: Any, **extra: Any) -> None:
        entry: dict[str, Any]
        if isinstance(wave, dict):
            entry = {
                "reason": reason,
                "series_id": wave.get("series_id"),
                "map_number": wave.get("map_number"),
                "ts_ns": wave.get("ts_ns"),
            }
        else:
            entry = {
                "reason": reason,
                "series_id": None,
                "map_number": None,
                "ts_ns": None,
            }
        entry.update(extra)
        excluded.append(entry)

    rows: list[dict] = []
    excluded: list[dict] = []

    for wave in waves:
        # 0) Shape: dict, sane numerics, hashable identity triple.
        if not isinstance(wave, dict):
            _exclude(REASON_MALFORMED, wave)
            continue
        series_id = wave.get("series_id")
        map_number = wave.get("map_number")
        ts_ns = wave.get("ts_ns")
        if not (_identity_element(series_id)
                and _identity_element(map_number)
                and _identity_element(ts_ns)):
            # Unhashable containers (list/dict), floats, bools, and nulls all
            # fail the identity gate BEFORE building any tuple key or touching
            # dict lookups, where an unhashable value would raise TypeError
            # and kill the whole run.
            _exclude(REASON_MALFORMED, wave)
            continue
        p_map = _safe_float(wave.get("p_map"))
        rise = _safe_float(wave.get("rise"))
        if not _is_unit_interval(p_map) or not _is_unit_interval(rise):
            # p_map=7.0 or rise=-0.5 is definitionally corrupted model output;
            # averaging it into brier / calib_gap would poison every metric,
            # including the headline settled_book_edge, with silent garbage.
            _exclude(REASON_MALFORMED, wave)
            continue

        # 1) Recover the team from the mapentry corpus by exact identity match.
        key = (series_id, map_number, ts_ns)
        team = team_index.get(key)
        if team is None:
            _exclude(REASON_NO_TEAM, wave)
            continue

        # 2) Settled verdict from the winners file ONLY (never inferred).
        verdict = winners.get(f"{series_id}:{map_number}")
        if not isinstance(verdict, dict) or verdict.get("winner") is None:
            _exclude(REASON_NO_VERDICT, wave)
            continue
        winner = verdict.get("winner")
        loser = verdict.get("loser")

        # 3) Exact-name membership; a team in neither slot is never guessed.
        if team == winner:
            won = 1.0
        elif team == loser:
            won = 0.0
        else:
            _exclude(REASON_NOT_IN_VERDICT, wave, team=team,
                     winner=winner, loser=loser)
            continue

        book_ask = _safe_float(wave.get("book_ask"))
        if not _is_unit_interval(book_ask):
            # absent/null book_ask is normal (an unpriced wave), but a PRESENT
            # price outside [0, 1] is corrupt capture: it would enter
            # settled_book_edge as the per-$1 stake paid at wave time, making
            # the headline number meaningless.  Null it out rather than
            # poisoning the n_book subset — the wave keeps its model metrics.
            book_ask = None

        rows.append({
            "series_id": series_id,
            "map_number": map_number,
            "team": team,
            "ts_ns": ts_ns,
            "rise": rise,
            "p_map": p_map,
            "book_ask": book_ask,
            "won": won,
            "overtime": verdict.get("overtime"),
        })

    # Sort with a total-ordering fallback: identity values can mix str series
    # ids with int ones, which Python 3 refuses to compare directly.  Giant
    # ints overflow float(), so numeric coercion gets its own guard.
    def _row_fields(row: dict) -> tuple[Any, Any, Any]:
        return (row["series_id"], row["map_number"], row["ts_ns"])

    def _num(v: Any) -> float:
        """Ordering float for an int identity field; failures sort last."""
        try:
            return float(v)
        except (ValueError, TypeError, OverflowError):
            return math.inf

    def _sort_key(fields: tuple[Any, Any, Any]) -> tuple:
        sid = fields[0]
        return (
            isinstance(sid, str),   # strs before ints; never compare mixed
            sid if isinstance(sid, str) else "",
            _num(fields[1]) if isinstance(fields[1], int)
            and not isinstance(fields[1], bool) else math.inf,
            _num(fields[2]) if isinstance(fields[2], int)
            and not isinstance(fields[2], bool) else math.inf,
        )

    rows.sort(key=lambda r: _sort_key(_row_fields(r)))
    excluded.sort(key=lambda e: _sort_key(
        (e.get("series_id"), e.get("map_number"), e.get("ts_ns"))))
    return rows, excluded


# ── metrics (pure) ─────────────────────────────────────────────────────────

def bucket_label(rise: float, rise_split: float) -> str:
    """Bucket label for a wave's rise: rise < split -> "lo", else "hi".

    The boundary is inclusive on the high side: rise == rise_split lands in
    "hi".  Intended for validated inputs only — callers guarantee finite
    values (the CLI rejects non-finite splits; join_waves drops non-finite
    rises as malformed), because NaN comparisons always route "hi".
    """
    return "lo" if rise < rise_split else "hi"


def _bucket_metrics(bucket_rows: list[dict]) -> dict:
    """Aggregate one bucket; empty buckets report n=0 and null metrics."""
    n = len(bucket_rows)
    if n == 0:
        return {
            "n": 0,
            "win_rate": None,
            "mean_p_map": None,
            "calib_gap": None,
            "brier": None,
            "n_book": 0,
            "mean_book_vs_model": None,
            "settled_book_edge": None,
        }
    win_rate = sum(r["won"] for r in bucket_rows) / n
    mean_p_map = sum(r["p_map"] for r in bucket_rows) / n
    # Belt-and-suspenders: the join stage already guarantees finite, in-range
    # numbers (see join_waves step 0 and the book_ask clamping note); treat
    # anything slipping past that as unpriced rather than averaging NaN in.
    book_rows = [
        r for r in bucket_rows
        if r["book_ask"] is not None
        and math.isfinite(r["book_ask"])
        and math.isfinite(r["p_map"])
    ]
    return {
        "n": n,
        "win_rate": round(win_rate, 6),
        "mean_p_map": round(mean_p_map, 6),
        "calib_gap": round(win_rate - mean_p_map, 6),
        "brier": round(sum((r["p_map"] - r["won"]) ** 2 for r in bucket_rows) / n, 6),
        "n_book": len(book_rows),
        "mean_book_vs_model": round(
            sum(r["book_ask"] - r["p_map"] for r in book_rows) / len(book_rows), 6
        ) if book_rows else None,
        "settled_book_edge": round(
            sum(r["won"] - r["book_ask"] for r in book_rows) / len(book_rows), 6
        ) if book_rows else None,
    }


def compute_buckets(rows: list[dict], rise_split: float) -> dict[str, dict]:
    """Compute per-bucket metrics over joined rows ("lo", "hi", "all")."""
    grouped: dict[str, list[dict]] = {"lo": [], "hi": []}
    for row in rows:
        grouped[bucket_label(row["rise"], rise_split)].append(row)
    grouped["all"] = rows
    return {label: _bucket_metrics(grouped[label]) for label in BUCKET_ORDER}


def validate_rise_split(text: str) -> float:
    """Parse --rise-split, rejecting NaN/inf with a clean argparse-style exit.

    Non-finite thresholds are outright operator error: nan makes every
    comparison false (everything lands in "hi") and both nan and inf hit
    params, where json.dumps emits bare NaN/Infinity — invalid JSON for strict
    parsers.  Fail loudly here instead of emitting poisoned artifacts.
    """
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid float value: {text!r}")
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError(
            f"rise-split must be finite, got {text!r}"
        )
    return value


# ── CLI entry point ────────────────────────────────────────────────────────

def _fmt(x: float | None) -> str:
    """Format a metric for the one-line stderr summary."""
    return f"{x:.4f}" if x is not None else "null"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Join wave detections to settled map winners and measure "
                    "realized book-vs-model edge."
    )
    parser.add_argument("waves", help="waves JSON file (output of wave_detect.py)")
    parser.add_argument("mapentry", help="mapentry JSONL file(s) carrying team names")
    parser.add_argument("winners", help="winners JSON file (output of grid_winners.py)")
    parser.add_argument(
        "--rise-split", type=validate_rise_split, default=DEFAULT_RISE_SPLIT,
        help=f"rise threshold: lo < split <= hi (default {DEFAULT_RISE_SPLIT})",
    )
    parser.add_argument("--out", help="also write the JSON result to this file "
                                      "(refuses to overwrite an existing file)")
    args = parser.parse_args(argv)

    try:
        waves, skipped_waves = load_waves(args.waves)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"wave_edge_join: cannot load {args.waves}: {exc}", file=sys.stderr)
        return 1
    try:
        entries, mapentry_problems = load_mapentry(args.mapentry)
        winners, skipped_winners = load_winners(args.winners)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"wave_edge_join: cannot load inputs: {exc}", file=sys.stderr)
        return 1

    team_index = build_team_index(entries)
    rows, excluded = join_waves(waves, team_index, winners)
    buckets = compute_buckets(rows, args.rise_split)

    result = {
        "buckets": buckets,
        "rows": rows,
        "excluded": excluded,
        "params": {
            "rise_split": args.rise_split,
            "waves_loaded": len(waves),
            "skipped_wave_rows": skipped_waves,
            "mapentry_rows_loaded": len(entries),
            "mapentry_line_problems": len(mapentry_problems),
            "skipped_winner_entries": skipped_winners,
        },
        # Not part of the core contract shape ({buckets, rows, excluded,
        # params}): keeps corrupt-line diagnostics visible without breaking
        # consumers expecting exactly the four documented keys.
        "_warnings": [{"event": "warning", "message": msg}
                      for msg in mapentry_problems],
    }

    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)

    if args.out:
        try:
            with open(args.out, "x", encoding="utf-8") as fh:
                fh.write(text)
        except FileExistsError:
            # Refuse to clobber prior analysis artifacts (corpus safety).
            print(f"wave_edge_join: --out file already exists: {args.out}",
                  file=sys.stderr)
            return 1
        except OSError as exc:
            print(f"wave_edge_join: cannot write {args.out}: {exc}", file=sys.stderr)
            return 1

    parts = [f"joined={len(rows)} excluded={len(excluded)}"]
    for label in BUCKET_ORDER:
        b = buckets[label]
        parts.append(
            f"{label}: n={b['n']} n_book={b['n_book']} "
            f"settled_book_edge={_fmt(b['settled_book_edge'])}"
        )
    print("wave_edge_join: " + " | ".join(parts), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
