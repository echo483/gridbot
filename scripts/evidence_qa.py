"""Deterministic, no-LLM evidence-flow QA for gridbot's capture tapes.

Process-liveness and directory-mtime checks (scripts/capture_qa.py) cannot see
a venue freeze: the PMUS recorder writes a heartbeat row every HEARTBEAT_S per
slug whether or not the book moved, so data/pmus/books-*.jsonl never goes
mtime-stale during a total venue freeze. Freshness and evidence are decoupled
by design on the exact tape that collapsed 2026-09-05/06.

This script measures whether EVIDENCE is flowing, not whether processes are
alive. It is deterministic and LLM-free: every ratio is compared against a
trailing baseline, never against absolute silence (legitimate overnight gaps
reach 454 minutes on an ordinary thin slate). It also separates two failures
that must never be conflated:

  - capture broke  => row counts fall INCLUDING heartbeats (or the file stops
                      growing); heartbeats per slug collapse.
  - venue went quiet => heartbeats keep flowing normally, non-heartbeat rows
                      collapse.

A fourth check closes the hole that day-scoping opens: because checks 1-3 are
keyed on days that EXIST in their tape, a tape that STOPS being written emits no
recent day, nothing flags, and the unit goes green — silence read as health. See
check_tape_silence().

Run: uv run python scripts/evidence_qa.py [--json] [--today YYYYMMDD]
Exit: 0 when all checks pass, 1 when a check flags within the RECENT window
(today or yesterday) OR a tape has gone silent. The day-keyed part is
deliberately NOT 'did anything ever flag' — 2026-09-05 is a real past incident
that sits in the tape permanently, and a unit red forever gets ignored within a
week. The printed table still shows the full history; see flagged_recently().
``--today`` pins the calendar for replay, and keeps the tests off the wall clock.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gridbot import config

try:
    from _jsonl import read_rows  # run as `python scripts/evidence_qa.py`
except ImportError:  # imported as scripts.evidence_qa (tests)
    from scripts._jsonl import read_rows

# --- Check 1: venue book-change rate (data/pmus/books-*.jsonl) -------------
# Metric: non-heartbeat rows / distinct slugs, per day. Flag when the day's
# ratio falls below CHECK1_RATIO_FLOOR * the trailing median of prior days
# whose slate is comparable in size — a 9-slug day and a 100-slug day are not
# comparable, so the comparison is guarded by slate size.
CHECK1_WINDOW_DAYS = 7      # trailing window of prior days for the baseline
CHECK1_RATIO_FLOOR = 0.25   # flag when day ratio < this fraction of the median
CHECK1_SLATE_FACTOR = 3.0   # prior day comparable iff its slate is within this factor
CHECK1_HB_FRACTION = 0.5    # verdict: heartbeats >= this * baseline => venue quiet
CHECK1_SPAN_FRACTION = 0.5  # observed span < this * baseline span => capture broke

# --- Check 2: postquote degeneracy (data/shadow/postquote-*.jsonl) ---------
# Metric: fraction of NON-NULL oriented_move values that are exactly 0.0.
# Nulls are excluded from the denominator (a fetch failure is not a zero).
CHECK2_THRESHOLD = 0.60     # flag when zero-fraction exceeds this
CHECK2_MIN_SAMPLES = 30     # ... and there are at least this many non-null values

# --- Check 3: gate-passing crossings (data/executor/intents-*.jsonl) -------
# Metric: rows whose status does not start with "skipped:". Flag after this
# many consecutive days of 0 crossings, guarded by a non-trivial book slate so
# a genuinely dead slate does not false-positive.
CHECK3_CONSECUTIVE = 2
CHECK3_MIN_SLATE = 5        # a day's distinct-slug count must be >= this to count

# --- Check 4: tape silence (all three tapes) -------------------------------
# Checks 1-3 are each keyed on days that EXIST in their tape, and the exit code
# is scoped to recent days (flagged_recently). A tape that stops being written
# therefore emits no recent day at all, nothing flags, and the unit goes GREEN:
# the watchdog reads SILENCE as health. That is the one failure an evidence-flow
# monitor must never have, because it is indistinguishable from the healthy case
# in every other check. This check asserts each tape is speaking, independent of
# what it says. It is a BACKSTOP, not the primary liveness alarm -- gridbot-health
# runs every 5 minutes; this catches the case where a process is alive and
# writing nothing.

# --- Reporting -------------------------------------------------------------
REPORT_DAYS = 7             # trailing days shown per check
PROVISIONAL_NIGHTS = 3      # nights the thresholds were calibrated on
PROVISIONAL_NOTE = (
    "All thresholds are PROVISIONAL, derived from {n} nights of tape. "
    "{n} nights is not a statistically defensible bound; re-derive the "
    "thresholds once more nights accumulate."
)


def _day(path: Path) -> str:
    """Extract the YYYYMMDD day from a `prefix-YYYYMMDD.jsonl` filename."""
    return path.stem.rsplit("-", 1)[-1]


def books_day(path: Path) -> dict:
    """Per-day metrics for the venue BBO tape.

    Combined (all kinds) and per-kind (match/map) non-heartbeat counts and
    distinct slugs, plus heartbeat and total row counts for the
    capture-broke vs venue-quiet discriminator. Distinct slugs are collected
    from every pmus_bbo row (heartbeat or not): a slug that only produced
    heartbeats is still a slug seen and correctly drags the ratio down.
    """
    nonhb = 0
    hb = 0
    slugs: set[str] = set()
    per_kind_nonhb: dict[str, int] = {}
    per_kind_slugs: dict[str, set[str]] = {}
    ts_lo: int | None = None
    ts_hi: int | None = None
    for row in read_rows(path):
        if row.get("type") != "pmus_bbo":
            continue
        slug = row.get("slug")
        kind = row.get("kind")
        if slug:
            slugs.add(slug)
            if kind:
                per_kind_slugs.setdefault(kind, set()).add(slug)
        ts = row.get("ts_ns")
        if isinstance(ts, int):
            ts_lo = ts if ts_lo is None else min(ts_lo, ts)
            ts_hi = ts if ts_hi is None else max(ts_hi, ts)
        if row.get("heartbeat"):
            hb += 1
            continue
        nonhb += 1
        if kind:
            per_kind_nonhb[kind] = per_kind_nonhb.get(kind, 0) + 1
    slate = len(slugs)
    return {
        "day": _day(path),
        "total": nonhb + hb,
        "hb": hb,
        "nonhb": nonhb,
        "slate": slate,
        "ratio": nonhb / slate if slate else 0.0,
        "hb_per_slug": hb / slate if slate else 0.0,
        # How much of the day we actually observed. A recorder that ran for
        # 6 of 24 hours produces normal heartbeat DENSITY over the hours it
        # ran, so density alone cannot tell our outage from a venue freeze.
        "span_s": (ts_hi - ts_lo) / 1e9 if ts_lo is not None and ts_hi is not None else None,
        "per_kind": {
            kind: {
                "nonhb": per_kind_nonhb.get(kind, 0),
                "slugs": len(per_kind_slugs.get(kind, set())),
                "ratio": (
                    per_kind_nonhb.get(kind, 0) / len(per_kind_slugs[kind])
                    if per_kind_slugs.get(kind)
                    else 0.0
                ),
            }
            for kind in sorted(set(per_kind_nonhb) | set(per_kind_slugs))
        },
    }


def postquote_day(path: Path) -> dict:
    """Per-day metrics for the adverse-selection diagnostic tape."""
    zero = 0
    nonnull = 0
    for row in read_rows(path):
        if row.get("type") != "postquote":
            continue
        move = row.get("oriented_move")
        if move is None:
            continue  # fetch failure — excluded from the denominator
        nonnull += 1
        if move == 0.0:
            zero += 1
    return {"day": _day(path), "zero": zero, "nonnull": nonnull}


def intents_day(path: Path) -> dict:
    """Per-day count of gate-passing crossings in the dry-run intent ledger."""
    gate_passing = 0
    total = 0
    for row in read_rows(path):
        if row.get("type") != "order_intent":
            continue
        total += 1
        if not str(row.get("status", "")).startswith("skipped:"):
            gate_passing += 1
    return {"day": _day(path), "gate_passing": gate_passing, "total": total}


def _within_factor(a: float, b: float, factor: float) -> bool:
    return a == b or max(a, b) / min(a, b) <= factor


def check_venue_book_change(days: list[dict]) -> list[dict]:
    """Flag each day whose non-heartbeat ratio collapses vs a comparable baseline.

    A day with no slate (no distinct slugs) is not a fault signal by itself and
    has no denominator to normalize against, so it is never flagged. When a day
    IS flagged, the verdict separates "venue went quiet" (heartbeats still
    flowing) from "our capture broke" (heartbeats collapsed too).
    """
    out: list[dict] = []
    for i, d in enumerate(days):
        prior = days[max(0, i - CHECK1_WINDOW_DAYS):i]
        comparable = [
            p for p in prior
            if p["slate"] > 0 and d["slate"] > 0
            and _within_factor(p["slate"], d["slate"], CHECK1_SLATE_FACTOR)
        ]
        if not comparable:
            out.append({**d, "flag": False, "verdict": "insufficient_baseline",
                        "baseline_median": None, "hb_baseline_median": None,
                        "span_baseline_median": None})
            continue
        median = statistics.median(p["ratio"] for p in comparable)
        if d["ratio"] >= CHECK1_RATIO_FLOOR * median:
            out.append({**d, "flag": False, "verdict": "ok",
                        "baseline_median": median, "hb_baseline_median": None,
                        "span_baseline_median": None})
            continue
        hb_median = statistics.median(p["hb_per_slug"] for p in comparable)
        # Coverage is checked BEFORE heartbeat density. A partially-covered day
        # has normal — often higher — heartbeats per slug over the hours it ran,
        # so density alone would call our own outage a venue freeze. This is not
        # hypothetical: on 2026-09-05 the recorder started at 17:58Z (6.0h of
        # tape vs 23.7h on 09-06) and its hb/slug was 143.9 against a 92.9
        # baseline, while the record wrongly called it a venue-side collapse.
        spans = [p["span_s"] for p in comparable if p.get("span_s") is not None]
        span_median = statistics.median(spans) if spans else None
        if (
            d.get("span_s") is not None
            and span_median
            and d["span_s"] < CHECK1_SPAN_FRACTION * span_median
        ):
            verdict = "capture_broke"
        elif d["hb_per_slug"] >= CHECK1_HB_FRACTION * hb_median:
            verdict = "venue_quiet"
        else:
            verdict = "capture_broke"
        out.append({**d, "flag": True, "verdict": verdict,
                    "baseline_median": median, "hb_baseline_median": hb_median,
                    "span_baseline_median": span_median})
    return out


def check_postquote(days: list[dict]) -> list[dict]:
    """Flag days whose non-null oriented_move is overwhelmingly exactly 0.0."""
    out: list[dict] = []
    for d in days:
        frac = d["zero"] / d["nonnull"] if d["nonnull"] else 0.0
        flag = d["nonnull"] >= CHECK2_MIN_SAMPLES and frac > CHECK2_THRESHOLD
        out.append({**d, "frac": frac, "flag": flag})
    return out


def check_gate_passing(days: list[dict], books_slate: dict[str, int]) -> list[dict]:
    """Flag after CHECK3_CONSECUTIVE consecutive days of 0 gate-passing crossings.

    A day only counts toward the streak if it ALSO had a non-trivial book slate,
    so a genuinely dead slate (no markets to cross) does not false-positive.
    """
    out: list[dict] = []
    streak = 0
    for d in days:
        if d["gate_passing"] == 0 and books_slate.get(d["day"], 0) >= CHECK3_MIN_SLATE:
            streak += 1
        else:
            streak = 0
        out.append({**d, "slate": books_slate.get(d["day"], 0),
                    "flag": streak >= CHECK3_CONSECUTIVE})
    return out


def build_report(books_days: list[dict], postquote_days: list[dict],
                 intents_days: list[dict], today: str | None = None) -> dict:
    """Assemble the full report from parsed per-day metrics.

    ``today`` is injectable so the report is testable against a fixed calendar;
    left None it reads the wall clock. Two end-to-end tests silently went green
    for a week because they asserted on a wall-clock window with fixed fixture
    days, and then failed the moment the calendar rolled past them.
    """
    check1 = check_venue_book_change(books_days)
    check2 = check_postquote(postquote_days)
    books_slate = {d["day"]: d["slate"] for d in books_days}
    check3 = check_gate_passing(intents_days, books_slate)
    reported = {
        "venue_book_change": check1[-REPORT_DAYS:],
        "postquote_degeneracy": check2[-REPORT_DAYS:],
        "gate_passing": check3[-REPORT_DAYS:],
    }
    # Liveness is measured on the FULL day lists, not the reported tail: the
    # question is whether the tape spoke recently, which a trailing slice
    # cannot make worse but should not be trusted to answer.
    silence = check_tape_silence(
        {
            "pmus/books": [(d["day"], d["total"]) for d in books_days],
            "shadow/postquote": [(d["day"], d["nonnull"]) for d in postquote_days],
            "executor/intents": [(d["day"], d["total"]) for d in intents_days],
        },
        today=today,
    )
    return {
        "provisional": {
            "nights": PROVISIONAL_NIGHTS,
            "note": PROVISIONAL_NOTE.format(n=PROVISIONAL_NIGHTS),
        },
        "checks": {
            "venue_book_change": {
                "metric": "non-heartbeat rows / distinct slugs (per day)",
                "days": reported["venue_book_change"],
            },
            "postquote_degeneracy": {
                "metric": "fraction of non-null oriented_move == 0.0",
                "days": reported["postquote_degeneracy"],
            },
            "gate_passing": {
                "metric": "rows whose status does not start with 'skipped:'",
                "days": reported["gate_passing"],
            },
            "tape_silence": {
                "metric": "newest day with rows, per tape (silence backstop)",
                "tapes": silence,
            },
        },
        # Scoped to recent days on purpose — see flagged_recently(). Silence is
        # OR'd in separately BECAUSE it is not day-scoped: a tape that stopped
        # being written has no recent day for flagged_recently() to find.
        "flagged": (
            flagged_recently(
                reported["venue_book_change"],
                reported["postquote_degeneracy"],
                reported["gate_passing"],
                today=today,
            )
            or any(t["flag"] for t in silence)
        ),
        "flagged_any_day": (
            any(t["flag"] for t in silence)
            or any(d["flag"] for d in reported["venue_book_change"])
            or any(d["flag"] for d in reported["postquote_degeneracy"])
            or any(d["flag"] for d in reported["gate_passing"])
        ),
    }


def _kind_str(per_kind: dict) -> str:
    match = per_kind.get("match")
    map_ = per_kind.get("map")
    m = f"{match['ratio']:.1f}" if match else "—"
    g = f"{map_['ratio']:.1f}" if map_ else "—"
    return f"{m} / {g}"


def _fmt_human(report: dict) -> str:
    lines = ["evidence_qa — evidence-flow QA"]
    lines.append(
        f"  PROVISIONAL thresholds, n={report['provisional']['nights']} nights "
        "(not statistically defensible — re-derive as more nights accumulate)"
    )
    c1 = report["checks"]["venue_book_change"]
    lines.append("check 1 venue book-change rate — non-heartbeat rows / distinct slugs")
    lines.append("  day      slate  ratio  hb/slug  baseline  flag   verdict   match/map")
    for d in c1["days"]:
        base = f"{d['baseline_median']:.1f}" if d["baseline_median"] is not None else "—"
        flag = "FLAG" if d["flag"] else "ok"
        lines.append(
            f"  {d['day']}  {d['slate']:>5}  {d['ratio']:6.1f}  "
            f"{d['hb_per_slug']:7.1f}  {base:>8}  {flag:>4}  "
            f"{d['verdict']:<19} {_kind_str(d['per_kind'])}"
        )
    c2 = report["checks"]["postquote_degeneracy"]
    lines.append("check 2 postquote degeneracy — zero / non-null oriented_move")
    lines.append("  day      zero  nonnull   frac  flag")
    for d in c2["days"]:
        flag = "FLAG" if d["flag"] else "ok"
        lines.append(
            f"  {d['day']}  {d['zero']:>5}  {d['nonnull']:>7}  "
            f"{d['frac'] * 100:5.1f}%  {flag}"
        )
    c3 = report["checks"]["gate_passing"]
    lines.append("check 3 gate-passing crossings — status not starting with 'skipped:'")
    lines.append("  day      passing  slate  flag")
    for d in c3["days"]:
        flag = "FLAG" if d["flag"] else "ok"
        lines.append(f"  {d['day']}  {d['gate_passing']:>7}  {d['slate']:>5}  {flag}")
    c4 = report["checks"]["tape_silence"]
    lines.append("check 4 tape silence — newest day with rows (backstop)")
    lines.append("  tape                newest    cutoff    flag")
    for t in c4["tapes"]:
        flag = "FLAG" if t["flag"] else "ok"
        lines.append(
            f"  {t['tape']:<18}  {t['newest_day'] or '—':<8}  "
            f"{t['cutoff']:<8}  {flag}"
        )
    lines.append("RESULT: " + ("FLAGGED (exit 1)" if report["flagged"]
                               else "all checks pass (exit 0)"))
    return "\n".join(lines)


# today + yesterday. Two days because the checks are keyed on UTC day files
# while the timer fires on local time, so 'today' flips mid-run for part of
# the day; a one-day window would miss a collapse that starts in the evening
# local and lands in the next UTC file. Not measured — stated as reasoning,
# unlike the ratio thresholds above, which carry measured baselines.
RECENT_DAYS = 2


def _recent_cutoff(today: str | None = None) -> str:
    """The oldest day still inside the RECENT window, as YYYYMMDD.

    Shared by flagged_recently() and check_tape_silence() so the exit code and
    the silence backstop can never disagree about what "recent" means.
    """
    if today is None:
        today = datetime.now(UTC).strftime("%Y%m%d")
    return (datetime.strptime(today, "%Y%m%d").replace(tzinfo=UTC)
            - timedelta(days=RECENT_DAYS - 1)).strftime("%Y%m%d")


def check_tape_silence(tapes: dict[str, list[tuple[str, int]]],
                       today: str | None = None) -> list[dict]:
    """Flag any tape whose newest ROW-BEARING day falls outside the recent window.

    ``tapes`` maps a tape name to ``(day, n_rows)`` pairs. A day with zero rows
    does not count as speaking: a file that exists and is empty is silence with
    extra steps. A tape with no days at all flags too — never having spoken is
    not health.

    Grace is a full day (RECENT_DAYS) because the day files roll on UTC while
    the slate is diurnal, so a tape can legitimately be empty for the first
    hours of a new UTC day. That makes this a slow alarm by construction; it is
    the backstop under the 5-minute health check, not a replacement for it.
    """
    cutoff = _recent_cutoff(today)
    out: list[dict] = []
    for name in sorted(tapes):
        days = [d for d, n in tapes[name] if n > 0]
        newest = max(days) if days else None
        out.append({
            "tape": name,
            "newest_day": newest,
            "cutoff": cutoff,
            "flag": newest is None or newest < cutoff,
        })
    return out


def flagged_recently(*day_lists, today: str | None = None) -> bool:
    """Whether any check flags within the RECENT window.

    This drives the process exit code, and therefore whether systemd shows the
    unit failed. It is deliberately NOT "did anything ever flag": 2026-09-05 is a
    real past incident that sits in the window permanently, and a unit that is
    red forever gets ignored within a week — worse than no alarm at all. The
    printed table still carries the full history for context; only the exit code
    is scoped. ``flagged_any_day`` in the report keeps the unscoped answer.
    """
    cutoff = _recent_cutoff(today)
    return any(
        d.get("flag") and str(d.get("day", "")) >= cutoff
        for days in day_lists for d in days
    )


# The baseline looks back CHECK1_WINDOW_DAYS and the table shows REPORT_DAYS, so
# nothing older than their sum can affect any output. Parsing the whole corpus
# anyway made this script O(all-time) for an O(14 days) question: measured
# 2026-09-07, a run read ~18.4 MB across the three globs, hourly, of which all but
# today's (and a just-rolled-over yesterday's) file is immutable and re-read for
# nothing. These directories only ever grow — pmus/books grew 5.5x in one day.
SCAN_DAYS = CHECK1_WINDOW_DAYS + REPORT_DAYS + 1


def _recent_files(directory: Path, prefix: str) -> list[Path]:
    """The newest SCAN_DAYS day files for a tape. Day files sort by name."""
    return sorted(directory.glob(f"{prefix}-*.jsonl"))[-SCAN_DAYS:]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--today", default=None, metavar="YYYYMMDD",
        help="pin the calendar (replay a past run; keeps tests off the wall clock)",
    )
    args = ap.parse_args()

    books_days = [books_day(p) for p in _recent_files(config.DATA_DIR / "pmus", "books")]
    postquote_days = [
        postquote_day(p) for p in _recent_files(config.DATA_DIR / "shadow", "postquote")
    ]
    intents_days = [
        intents_day(p) for p in _recent_files(config.DATA_DIR / "executor", "intents")
    ]
    report = build_report(books_days, postquote_days, intents_days, today=args.today)

    if args.json:
        print(json.dumps(report, indent=1))
    else:
        print(_fmt_human(report))
    sys.exit(1 if report["flagged"] else 0)


if __name__ == "__main__":
    main()