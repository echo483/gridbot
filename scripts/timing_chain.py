#!/usr/bin/env python3
"""timing_chain.py — how long after a GRID round end does the PMUS book move?

Timing-chain instrument, part B (docs/superpowers/specs/2026-09-03-timing-chain.md).
Analysis only (slow loop, iron rail 1): local day files in, markdown + json
out, no network, no orders, no keys. It registers nothing and recommends
nothing — it produces the number a future rail-B (latency / model-momentum)
instrument would be pre-registered against.

Step 1 — round ends from GRID. Per (series, map) the capture daemon's ``ss``
polls are walked in ``req_ns`` order; when the count of resolved rounds
(``rows.round_winners``) rises by exactly one, an event is emitted bracketed
by the previous poll (``t_prev_ns``: round not yet over) and the observing
poll (``t_obs_ns``). TRAP D1: the winner NAME is read from the round's own
segment (``won`` flag), never from side->team on the observing snapshot,
whose sides already belong to the NEXT round (halftime swap 12->13).

Step 2 — first book move. ``mapentry.market_for_instance`` gives the live
map's slug and orientation (``long`` = the winner is the book's named team,
so the expected reprice is mid UP; ``short`` = mid DOWN). On the
``pmus_bbo`` tape (heartbeats included — they are valid observations) the
pre-event mid is the last row at or before ``t_prev``; the first row after
``t_prev`` whose mid differs by >= one tick (0.01), searched up to
``t_obs + 300s``, is the move. The first read
(docs/analysis/2026-09-03-timing-chain-first-read.md) found that first
move agrees with the round winner only ~55% of the time, so every joined
event also carries the size-and-horizon read of Step 2b.

Step 2b — size and horizon reading. ``move_30s`` / ``move_60s`` /
``move_120s``: mid of the last ``pmus_bbo`` row with ``req_ns <= t_obs +
h`` minus the pre-event mid, ORIENTED toward the round winner's side
(us_side long -> +delta, short -> -delta); None at a horizon whose window
(t_obs, t_obs + h] holds no row — the pre-event row alone is not an
observation at that horizon.
``t_move3_ns`` / ``lead3_vs_obs_s`` / ``direction3_agrees``: the first
>=3-tick (0.03) move, same semantics as the 1-tick fields. Summary
intervals are series-clustered bootstraps (resample series_id with
replacement, 1000 reps, random.Random(17)); n and n_series ride with
every CI — never pooled without an interval.

Step 3 — our own pipeline legs on the same events (instances, mapentry
rows, order intents): reported where joinable, never imputed.

TRAP D2: ``lead_vs_obs_s`` is a LOWER bound on the true GRID lead — round
ends are seen at poll granularity (12s live cadence) and the book at the 2s
recorder cadence. Never compare it to the cs2agent-era intl 13-27s figure
as if it were the same measurement. The last round of a map never emits an
event (the game is already finished on the observing snapshot).

Usage:
    uv run python scripts/timing_chain.py YYYYMMDD [YYYYMMDD ...]

Exit 1, loudly, when a day has no ``pmus_bbo`` rows: an empty measurement
must never print a green summary.
"""

from __future__ import annotations

import json
import random
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from gridbot import config
from gridbot.corpus import rows as r
from gridbot.instruments import mapentry

try:
    from _jsonl import read_rows  # run as `python scripts/timing_chain.py`
except ImportError:  # imported as scripts.timing_chain (tests)
    from scripts._jsonl import read_rows

TICK = 0.01
TICK_EPS = 1e-9
MOVE3_TICKS = 3                # "real" move = >= 3 ticks = 0.03 in mid
MOVE_WINDOW_S = 300.0
HORIZONS_S = (30, 60, 120)     # oriented mid-move horizons after t_obs
COVERAGE_SLACK_S = 35.0        # recorder heartbeat (30s) + cycle jitter: a covered slug always has a row this recent
BOOTSTRAP_SEED = 17            # series-clustered CI resampling
BOOTSTRAP_REPS = 1000
INTENT_JOIN_WINDOW_S = 120.0  # an intent this long after its mapentry row is a different event
PHASES = (("early (round <= 6)", 1, 6), ("mid (7-18)", 7, 18), ("late (>= 19)", 19, 10**9))
LEGS = (
    ("grid_rtt_ms", "GRID round trip (ss.ts_ns - req_ns)"),
    ("detect_ms", "detect lag (instance.ts_ns - grid_ts_ns)"),
    ("recorder_ms", "recorder lag (mapentry.ts_ns - src_ts_ns)"),
    ("engine_ms", "engine lag (intent.ts_ns - mapentry.ts_ns)"),
    ("total_ms", "total (intent.ts_ns - t_obs_ns)"),
)
CAVEAT = (
    "lead_vs_obs_s is a LOWER bound on the true GRID lead: round ends are seen at poll "
    "granularity (12s live cadence) and the book at the 2s recorder cadence. NEGATIVE means "
    "the book repriced before our poll even showed the round end. Not comparable to the "
    "intl 13-27s figure — different venue, different measurement."
)


# --- inputs ------------------------------------------------------------------


def load_rows(path: Path) -> list[dict]:
    return list(read_rows(path)) if path.exists() else []


def prev_day(day: str) -> str:
    d = date(int(day[:4]), int(day[4:6]), int(day[6:8]))
    return f"{d - timedelta(days=1):%Y%m%d}"


def load_mappings(pmus_dir: Path, day: str) -> dict[str, dict]:
    """grid_series_id -> newest matched mapping over day-1 and day (later snapshot wins).

    Two files for the same reason as ``mapentry.load_latest_mappings``: a match
    made before UTC midnight must survive the rollover.
    """
    out: dict[str, dict] = {}
    for d in (prev_day(day), day):
        for rec in load_rows(pmus_dir / f"mappings-{d}.jsonl"):
            if rec.get("type") == "grid_pmus_mapping" and rec.get("match"):
                out[str(rec.get("grid_series_id"))] = rec
    return out


# --- step 1: round-end events ------------------------------------------------


def segment_winner(game: dict, n: int) -> tuple[str, str] | None:
    """(winner_name, loser_name) from round segment ``n``'s own ``won`` flags (D1)."""
    for seg in game.get("segments") or []:
        if str(seg.get("type") or "").lower() != "round":
            continue
        if int(seg.get("sequenceNumber") or 0) != n:
            continue
        teams = seg.get("teams") or []
        won = [t for t in teams if t.get("won")]
        if len(won) != 1:
            return None
        winner = str(won[0].get("name") or "")
        if not winner:
            return None
        losers = [str(t.get("name") or "") for t in teams if t is not won[0]]
        if not losers:
            losers = [str(t.get("name") or "") for t in game.get("teams") or []
                      if str(t.get("name") or "") != winner]
        return winner, losers[0] if losers else ""
    return None


def round_end_events(ss_rows: list[dict],
                     test_ids: frozenset[str] = config.GRID_TEST_SERIES_IDS) -> list[dict]:
    """One event per resolved round observed between consecutive polls of a (series, map)."""
    ordered = sorted(
        (row for row in ss_rows
         if row.get("type") == "ss" and isinstance(row.get("req_ns"), int)),
        key=lambda row: row["req_ns"],
    )
    # Per (series, map): (positional resolved count, req_ns, resolved segment seqs).
    # The COUNT is positional (rows.round_winners skips a finished segment whose
    # winner is unresolvable) — it is what instance/mapentry rows carry as
    # round_number, so it stays the join key. The winner is looked up by the
    # segment's OWN sequenceNumber (rows.round_winners_by_seq): after such a
    # hole, count n no longer names segment n.
    last: dict[tuple[str, int], tuple[int, int, frozenset[int]]] = {}
    events: list[dict] = []
    for row in ordered:
        sid = str(row.get("series_id") or "")
        if not sid or sid in test_ids:
            continue
        state = (row.get("data") or {}).get("seriesState") or {}
        game = r.current_game(state)
        if game is None:
            continue
        seq = game.get("sequenceNumber")
        if seq is None:
            continue
        map_number = int(seq)
        n = len(r.round_winners(game))
        resolved = frozenset(r.round_winners_by_seq(game))
        t = row["req_ns"]
        key = (sid, map_number)
        prev = last.get(key)
        last[key] = (n, t, resolved)
        if prev is None:  # first observation of this map: no bracket
            continue
        prev_n, t_prev, prev_resolved = prev
        if n <= prev_n:
            continue
        ts = row.get("ts_ns")
        new_segs = sorted(resolved - prev_resolved)
        ev = {
            "series_id": sid,
            "map_number": map_number,
            "round_number": n,
            "round_seq": new_segs[0] if len(new_segs) == 1 else None,
            "winner_team": None,
            "loser_team": None,
            "t_prev_ns": t_prev,
            "t_obs_ns": t,
            "poll_gap_s": round((t - t_prev) / 1e9, 3),
            "outcome": None,
            "legs": {"grid_rtt_ms": (ts - t) / 1e6 if isinstance(ts, int) else None},
        }
        if n - prev_n > 1 or len(new_segs) > 1:
            ev["outcome"] = "skipped:multi_round_gap"
        else:
            named = segment_winner(game, new_segs[0]) if new_segs else None
            if named is None:
                ev["outcome"] = "skipped:ambiguous_winner"
            else:
                ev["winner_team"], ev["loser_team"] = named
        events.append(ev)
    return events


# --- step 2: first book move -------------------------------------------------


def book_series(bbo_rows: list[dict]) -> dict[str, tuple[list[int], list[float]]]:
    """slug -> (req_ns list, mid list), sorted; rows lacking a two-sided book carry no mid."""
    by: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for row in bbo_rows:
        if row.get("type") != "pmus_bbo":
            continue
        t, bid, ask = row.get("req_ns"), row.get("bid"), row.get("ask")
        if not isinstance(t, int):
            continue
        if not isinstance(bid, (int, float)) or not isinstance(ask, (int, float)):
            continue
        by[str(row.get("slug"))].append((t, (float(bid) + float(ask)) / 2.0))
    out: dict[str, tuple[list[int], list[float]]] = {}
    for slug, pts in by.items():
        pts.sort()
        out[slug] = ([t for t, _ in pts], [m for _, m in pts])
    return out


def first_move(times: list[int], mids: list[float], t_prev_ns: int, t_obs_ns: int,
               tick: float = TICK, window_s: float = MOVE_WINDOW_S) -> dict:
    """Pre-event mid, mid at observation, the first >= one-tick move after
    t_prev, and the first >=3-tick move (``*_3`` fields, same semantics).

    The 3-tick scan keys (``t_move3_ns``, ``mid_after_3``, ``move_size_3``)
    are always present and are None when no such move exists in the window —
    a 1- or 2-tick jitter never registers as a 3-tick move.
    """
    i = bisect_right(times, t_prev_ns)  # rows with req_ns <= t_prev are [0, i)
    if i == 0:
        return {"mid_before": None, "mid_at_obs": None, "t_move_ns": None,
                "mid_after": None, "move_size": None,
                "t_move3_ns": None, "mid_after_3": None, "move_size_3": None}
    mid_before = mids[i - 1]
    j = bisect_right(times, t_obs_ns)
    mid_at_obs = mids[j - 1]
    limit = t_obs_ns + int(window_s * 1e9)
    out = {"mid_before": round(mid_before, 4), "mid_at_obs": round(mid_at_obs, 4),
           "t_move_ns": None, "mid_after": None, "move_size": None,
           "t_move3_ns": None, "mid_after_3": None, "move_size_3": None}
    tick3 = MOVE3_TICKS * tick
    for k in range(i, len(times)):
        if times[k] > limit:
            break
        delta = abs(mids[k] - mid_before)
        if out["t_move_ns"] is None and delta >= tick - TICK_EPS:
            out.update(t_move_ns=times[k], mid_after=round(mids[k], 4),
                       move_size=round(mids[k] - mid_before, 4))
        if out["t_move3_ns"] is None and delta >= tick3 - TICK_EPS:
            out.update(t_move3_ns=times[k], mid_after_3=round(mids[k], 4),
                       move_size_3=round(mids[k] - mid_before, 4))
        if out["t_move_ns"] is not None and out["t_move3_ns"] is not None:
            break
    return out


def horizon_moves(times: list[int], mids: list[float], t_obs_ns: int, mid_before: float,
                  horizons_s: tuple[int, ...] = HORIZONS_S,
                  slack_s: float = COVERAGE_SLACK_S) -> dict[str, float | None]:
    """Mid move at each horizon: mid(last row with req_ns <= t_obs + h) minus
    mid_before; None when the recorder was not covering the slug at the horizon.

    Positive = book moved toward the round winner's side once ``us_side`` is
    long; callers flip the sign for short. The tape is change-only with a
    heartbeat every HEARTBEAT_S per slug, so a COVERED slug always has a row
    within ``slack_s`` before any instant — a silent book is a real 0.0 move,
    not a missing observation. Requiring a row strictly inside (t_obs, t_obs+h]
    instead would drop exactly the quiet books and bias every horizon toward
    movers. A last row older than ``slack_s`` means a coverage gap: None.
    """
    out: dict[str, float | None] = {}
    slack_ns = int(slack_s * 1e9)
    for h in horizons_s:
        t_h = t_obs_ns + int(h * 1e9)
        hi = bisect_right(times, t_h)           # rows at or before the horizon are [0, hi)
        covered = hi > 0 and t_h - times[hi - 1] <= slack_ns
        out[f"move_{h}s"] = round(mids[hi - 1] - mid_before, 4) if covered else None
    return out


def join_books(events: list[dict], mappings: dict[str, dict],
               books: dict[str, tuple[list[int], list[float]]]) -> None:
    """Attach slug/orientation and the first-move outcome to each open event (in place)."""
    for ev in events:
        if ev["outcome"] is not None:
            continue
        mapping = mappings.get(ev["series_id"])
        picked = None
        if mapping is not None:
            picked = mapentry.market_for_instance(
                mapping, ev["map_number"], team=ev["winner_team"], opponent=ev["loser_team"])
        if picked is None:
            ev["outcome"] = "skipped:no_book"
            continue
        slug, us_side = picked
        ev["slug"], ev["us_side"] = slug, us_side
        series = books.get(slug)
        res = first_move(*series, ev["t_prev_ns"], ev["t_obs_ns"]) if series else None
        if series is None or res is None or res["mid_before"] is None:
            ev["outcome"] = "skipped:no_pre_book"
            continue
        ev.update(res)
        # Oriented horizon read (moved AND no_move_300s events): +delta toward
        # the winner's side (long -> up is good, short -> down is good).
        sign = 1.0 if us_side == "long" else -1.0
        for key, v in horizon_moves(*series, ev["t_obs_ns"], res["mid_before"]).items():
            ev[key] = None if v is None else round(sign * v, 4)
        if res["t_move_ns"] is None:
            ev["outcome"] = "no_move_300s"
            continue
        ev["outcome"] = "moved"
        ev["lead_vs_obs_s"] = round((res["t_move_ns"] - ev["t_obs_ns"]) / 1e9, 3)
        ev["lead_vs_prev_s"] = round((res["t_move_ns"] - ev["t_prev_ns"]) / 1e9, 3)
        up = res["move_size"] > 0
        ev["direction_agrees"] = (up and us_side == "long") or (not up and us_side == "short")
        if res["t_move3_ns"] is not None:  # a >=3-tick move implies the 1-tick one
            ev["lead3_vs_obs_s"] = round((res["t_move3_ns"] - ev["t_obs_ns"]) / 1e9, 3)
            up3 = res["move_size_3"] > 0
            ev["direction3_agrees"] = (up3 and us_side == "long") or (not up3 and us_side == "short")


# --- step 3: our own pipeline legs -------------------------------------------


def join_pipeline(events: list[dict], instances: list[dict], mapentries: list[dict],
                  intents: list[dict]) -> None:
    """Per-event detect / recorder / engine / total legs, where joinable (in place)."""
    inst_by: dict[tuple, list[dict]] = defaultdict(list)
    for row in instances:
        if row.get("type") == "instance" and isinstance(row.get("ts_ns"), int):
            inst_by[(str(row.get("series_id")), row.get("map_number"),
                     row.get("round_number"))].append(row)
    me_by: dict[tuple, list[dict]] = defaultdict(list)
    for row in mapentries:
        if row.get("type") == "mapentry" and isinstance(row.get("ts_ns"), int):
            me_by[(row.get("src_hypothesis_id"), str(row.get("series_id")),
                   row.get("map_number"), row.get("round_number"),
                   row.get("dedup_extra", ""))].append(row)
    intent_by: dict[str, list[dict]] = defaultdict(list)
    for row in intents:
        if row.get("type") == "order_intent" and isinstance(row.get("ts_ns"), int):
            intent_by[str(row.get("dedup_key"))].append(row)

    window_ns = int(INTENT_JOIN_WINDOW_S * 1e9)
    for ev in events:
        legs = ev.setdefault("legs", {})
        legs.update({"detect_ms": None, "recorder_ms": None, "engine_ms": None,
                     "total_ms": None})
        sid, map_number, rn = ev["series_id"], ev["map_number"], ev["round_number"] + 1
        insts = sorted(inst_by.get((sid, map_number, rn), []), key=lambda x: x["ts_ns"])
        if not insts:
            continue
        first = insts[0]
        if isinstance(first.get("grid_ts_ns"), int):
            legs["detect_ms"] = round((first["ts_ns"] - first["grid_ts_ns"]) / 1e6, 1)
        mes: list[dict] = []
        for inst in insts:
            mes.extend(me_by.get((inst.get("hypothesis_id"), sid, map_number, rn,
                                  inst.get("dedup_extra", "")), []))
        if not mes:
            continue
        mes.sort(key=lambda x: x["ts_ns"])
        if isinstance(mes[0].get("src_ts_ns"), int):
            legs["recorder_ms"] = round((mes[0]["ts_ns"] - mes[0]["src_ts_ns"]) / 1e6, 1)
        cands: list[tuple[int, int]] = []
        for me in mes:
            if not (me.get("us_side") and me.get("market_slug")):
                continue
            key = (f"{me.get('hypothesis_id')}:{sid}:{map_number}:"
                   f"{me['us_side']}:{me['market_slug']}")
            for it in intent_by.get(key, []):
                if it.get("round_number") != rn:
                    continue
                if me["ts_ns"] <= it["ts_ns"] <= me["ts_ns"] + window_ns:
                    cands.append((it["ts_ns"], me["ts_ns"]))
        if cands:
            it_ts, me_ts = min(cands)
            legs["engine_ms"] = round((it_ts - me_ts) / 1e6, 1)
            legs["total_ms"] = round((it_ts - ev["t_obs_ns"]) / 1e6, 1)


# --- summary + output --------------------------------------------------------


def pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (pos - lo), 3)


def _rep_stat(vals: list[float], statistic: str) -> float:
    if statistic == "median":
        s = sorted(vals)
        m = len(s) // 2
        return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0
    return sum(vals) / len(vals)


def bootstrap_ci(pairs: list[tuple[str, float]], statistic: str = "mean",
                 seed: int = BOOTSTRAP_SEED, reps: int = BOOTSTRAP_REPS) -> dict | None:
    """Point estimate and series-clustered 95% CI of a mean or median.

    Resamples SERIES with replacement (anti-overfitting rail: events of one
    series share a book and a winner; the cluster, not the row, is the
    observation unit), takes the statistic of the resampled events each rep,
    and reports the 2.5/97.5 percentile of the rep statistics. Deterministic
    for a fixed seed (17). None when there is nothing to estimate; ``ci95`` is
    None with a single series — one cluster carries no between-series
    information, and a zero-width interval would read as certainty.
    """
    if not pairs:
        return None
    by_series: dict[str, list[float]] = defaultdict(list)
    for sid, v in pairs:
        by_series[sid].append(v)
    sids = sorted(by_series)
    if len(sids) == 1:
        point = round(_rep_stat(by_series[sids[0]], statistic), 4)
        return {"point": point, "ci95": None, "n": len(pairs), "n_series": 1}
    rng = random.Random(seed)
    stats: list[float] = []
    for _ in range(reps):
        resampled: list[float] = []
        for _ in sids:
            resampled.extend(by_series[rng.choice(sids)])
        stats.append(_rep_stat(resampled, statistic))
    stats.sort()
    point = round(_rep_stat([v for _, v in pairs], statistic), 4)
    return {"point": point,
            "ci95": [round(stats[int(0.025 * (len(stats) - 1))], 4),
                     round(stats[int(0.975 * (len(stats) - 1))], 4)],
            "n": len(pairs), "n_series": len(sids)}


def _moved_block(evs: list[dict]) -> dict:
    leads = [ev["lead_vs_obs_s"] for ev in evs]
    n = len(evs)
    return {
        "n": n,
        "lead_vs_obs_s": {"p10": pct(leads, 0.1), "p50": pct(leads, 0.5), "p90": pct(leads, 0.9)},
        "lead_vs_prev_s_p50": pct([ev["lead_vs_prev_s"] for ev in evs], 0.5),
        "book_first_share": None if not n else round(sum(1 for x in leads if x < 0) / n, 3),
        "direction_agreement_rate": (
            None if not n else round(sum(1 for ev in evs if ev["direction_agrees"]) / n, 3)),
    }


def _moved3_block(evs: list[dict]) -> dict:
    """Same shape as the 1-tick moved block, restricted to events whose first
    >=3-tick move landed within 300s (a subset of outcome == moved)."""
    evs3 = [ev for ev in evs if "lead3_vs_obs_s" in ev]
    leads = [ev["lead3_vs_obs_s"] for ev in evs3]
    n = len(evs3)
    return {
        "n": n,
        "lead3_vs_obs_s": {"p10": pct(leads, 0.1), "p50": pct(leads, 0.5),
                           "p90": pct(leads, 0.9)},
        "book_first_share": None if not n else round(sum(1 for x in leads if x < 0) / n, 3),
        "direction_agreement_rate": (
            None if not n else round(sum(1 for ev in evs3 if ev["direction3_agrees"]) / n, 3)),
    }


def _horizon_block(evs: list[dict], h: int) -> dict:
    """Oriented move at horizon h across joined events (moved + no_move_300s)."""
    pairs = [(ev["series_id"], ev[f"move_{h}s"]) for ev in evs
             if ev.get(f"move_{h}s") is not None]
    return {
        "horizon_s": h,
        "n": len(evs),
        "n_with_row": len(pairs),
        "mean_oriented": bootstrap_ci(pairs, "mean"),
        "median_oriented": bootstrap_ci(pairs, "median"),
    }


def summarize(day: str, events: list[dict]) -> dict:
    outcomes = Counter(ev["outcome"] for ev in events)
    moved = [ev for ev in events if ev["outcome"] == "moved"]
    oriented = [ev for ev in events if ev["outcome"] in ("moved", "no_move_300s")]
    m120 = [ev["move_120s"] for ev in oriented if ev.get("move_120s") is not None]
    legs = {}
    for key, label in LEGS:
        vals = [ev["legs"][key] for ev in events if ev.get("legs", {}).get(key) is not None]
        legs[key] = {"label": label, "n": len(vals), "median_ms": pct(vals, 0.5)}
    return {
        "day": day,
        "caveat": CAVEAT,
        "n_round_ends": len(events),
        "n_joined": sum(1 for ev in events if ev.get("slug")),
        "outcomes": dict(sorted(outcomes.items())),
        "moved": _moved_block(moved),
        "moved3": _moved3_block(moved),
        "horizons": {
            "n": len(oriented),
            "by_horizon": {f"{h}s": _horizon_block(oriented, h) for h in HORIZONS_S},
            "share_oriented_120s_ge_3t": (None if not m120
                                          else round(sum(1 for x in m120 if x >= 3 * TICK - TICK_EPS)
                                                     / len(m120), 3)),
            "n_oriented_120s": len(m120),
        },
        "by_phase": {
            label: _moved_block([ev for ev in moved if lo <= ev["round_number"] <= hi])
            for label, lo, hi in PHASES
        },
        "legs": legs,
    }


def _fmt(v: object) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        s = f"{v:.3f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:.0f}"
        return "0" if s == "-0" else s
    return str(v)


def _fmt_ci(blk: dict | None) -> str:
    """point [lo, hi] with n and n_series beside the interval — never pooled without it."""
    if blk is None:
        return "—"
    if blk["ci95"] is None:
        return f"{_fmt(blk['point'])} [no interval: 1 series] (n={blk['n']}, series=1)"
    lo, hi = blk["ci95"]
    return (f"{_fmt(blk['point'])} [{_fmt(lo)}, {_fmt(hi)}] "
            f"(n={blk['n']}, series={blk['n_series']})")


def render(summary: dict) -> str:
    m = summary["moved"]
    lines = [
        f"# timing chain — {summary['day']}",
        "",
        f"> {CAVEAT}",
        "",
        f"- round ends observed (test series excluded): **{summary['n_round_ends']}**",
        f"- joined to a PMUS map book: **{summary['n_joined']}**",
        "",
        "## outcomes",
        "",
        "| outcome | n |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in summary["outcomes"].items()] or ["| (none) | 0 |"]
    lines += [
        "",
        f"## first book move (outcome = moved, n={m['n']})",
        "",
        "| stat | value |",
        "|---|---|",
        f"| lead_vs_obs_s p10 | {_fmt(m['lead_vs_obs_s']['p10'])} |",
        f"| lead_vs_obs_s p50 | {_fmt(m['lead_vs_obs_s']['p50'])} |",
        f"| lead_vs_obs_s p90 | {_fmt(m['lead_vs_obs_s']['p90'])} |",
        f"| lead_vs_prev_s p50 (upper bound) | {_fmt(m['lead_vs_prev_s_p50'])} |",
        f"| book first (lead_vs_obs_s < 0) | {_fmt(m['book_first_share'])} |",
        f"| direction agreement | {_fmt(m['direction_agreement_rate'])} |",
        "",
        f"## size and horizon reading (moved + no_move_300s, n={summary['horizons']['n']})",
        "",
        "Oriented mid move vs the pre-event mid (+ toward the round winner's side);",
        "series-clustered bootstrap 95% CI (resample series_id, 1000 reps, seed 17).",
        "",
        "| horizon | rows | mean oriented [95% CI] | median oriented [95% CI] |",
        "|---|---|---|---|",
    ]
    for h in HORIZONS_S:
        blk = summary["horizons"]["by_horizon"][f"{h}s"]
        lines.append(f"| +{h}s | {blk['n_with_row']}/{blk['n']} | "
                     f"{_fmt_ci(blk['mean_oriented'])} | {_fmt_ci(blk['median_oriented'])} |")
    m3 = summary["moved3"]
    hz = summary["horizons"]
    lines += [
        "",
        (f"- share of events with oriented +120s move >= +0.03: "
         f"**{_fmt(hz['share_oriented_120s_ge_3t'])}** (n={hz['n_oriented_120s']})"),
        "",
        f"## first >=3-tick move (n={m3['n']})",
        "",
        "| stat | value |",
        "|---|---|",
        f"| lead3_vs_obs_s p10 | {_fmt(m3['lead3_vs_obs_s']['p10'])} |",
        f"| lead3_vs_obs_s p50 | {_fmt(m3['lead3_vs_obs_s']['p50'])} |",
        f"| lead3_vs_obs_s p90 | {_fmt(m3['lead3_vs_obs_s']['p90'])} |",
        f"| book first (lead3_vs_obs_s < 0) | {_fmt(m3['book_first_share'])} |",
        f"| direction agreement | {_fmt(m3['direction_agreement_rate'])} |",
        "",
        "## by map phase (moved only)",
        "",
        "| phase | n | p50 lead_vs_obs_s | book first | agreement |",
        "|---|---|---|---|---|",
    ]
    for label, blk in summary["by_phase"].items():
        lines.append(f"| {label} | {blk['n']} | {_fmt(blk['lead_vs_obs_s']['p50'])} | "
                     f"{_fmt(blk['book_first_share'])} | {_fmt(blk['direction_agreement_rate'])} |")
    lines += [
        "",
        "## our pipeline legs (same events; median ms where joinable, never imputed)",
        "",
        "| leg | n joinable | median ms |",
        "|---|---|---|",
    ]
    missing = []
    for key, blk in summary["legs"].items():
        lines.append(f"| {blk['label']} | {blk['n']} | {_fmt(blk['median_ms'])} |")
        if blk["n"] == 0:
            missing.append(key)
    if missing:
        lines += ["", f"- missing legs (no joinable rows): {', '.join(missing)}"]
    return "\n".join(lines)


def run_day(day: str, data: Path) -> int:
    books_path = data / "pmus" / f"books-{day}.jsonl"
    bbo_rows = [row for row in load_rows(books_path) if row.get("type") == "pmus_bbo"]
    if not bbo_rows:
        print(
            f"!!! timing_chain {day}: NO pmus_bbo rows in {books_path} — the PMUS book "
            "recorder (gridbot.capture.pmus_books / gridbot-pmus-books.service) has not "
            "recorded this day. Nothing to measure; refusing to print a summary. Exit 1.",
            file=sys.stderr,
        )
        return 1
    events = round_end_events(load_rows(data / "grid" / f"series_state-{day}.jsonl"))
    join_books(events, load_mappings(data / "pmus", day), book_series(bbo_rows))
    join_pipeline(
        events,
        load_rows(data / "shadow" / f"instances-{day}.jsonl"),
        load_rows(data / "shadow" / f"mapentry-{day}.jsonl"),
        load_rows(data / "executor" / f"intents-{day}.jsonl"),
    )
    summary = summarize(day, events)
    print(render(summary))
    out_dir = data / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"timing_chain-{day}.json"
    out.write_text(json.dumps({"day": day, "events": events, "summary": summary}, indent=1))
    print(f"\nwrote {out} ({len(events)} events)")
    return 0


def main(argv: list[str] | None = None, data_dir: Path | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: timing_chain.py YYYYMMDD [YYYYMMDD ...]", file=sys.stderr)
        return 2
    days = []
    for arg in args:
        day = arg.replace("-", "")
        if len(day) != 8 or not day.isdigit():
            print(f"bad day {arg!r}: expected bare YYYYMMDD", file=sys.stderr)
            return 2
        days.append(day)
    data = Path(data_dir) if data_dir is not None else config.DATA_DIR
    rc = 0
    for i, day in enumerate(days):
        if i:
            print()
        rc = max(rc, run_day(day, data))
    return rc


if __name__ == "__main__":
    sys.exit(main())
