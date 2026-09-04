"""Hermetic tests for scripts/venue_pricing_cut.py on synthetic rows (no network)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import venue_pricing_cut as vpc

NS = 1_000_000_000
SID = "2999862"
MAP = 1
T_PREV = 1_000_000_000
T_OBS = 2_000_000_000
WIN, LOSE = "magic", "BIG"
MATCH_SLUG = "aec-cs2-mgc-big-2026-09-03"
DAY = "20260903"

# A valid (SID, MAP, T_OBS) score pair for helper events.
SCORES = {(SID, MAP, T_OBS): {WIN: 12, LOSE: 10}}


def _bbo(slug: str, req_ns: int, bid, ask, kind: str = "match",
         heartbeat: bool = False) -> dict:
    return {"type": "pmus_bbo", "slug": slug, "series_id": SID, "map_number": 3,
            "kind": kind, "bid": bid, "ask": ask, "req_ns": req_ns,
            "latency_ms": 80, "heartbeat": heartbeat, "ts_ns": req_ns + 80_000_000}


def _ss(req_ns: int, scores: dict, seq: int = MAP, sid=SID) -> dict:
    teams = [{"name": n, "score": sc} for n, sc in scores.items()]
    return {"type": "ss", "series_id": sid, "req_ns": req_ns,
            "data": {"seriesState": {"started": True, "finished": False,
                                     "teams": [{"name": n} for n in scores],
                                     "games": [{"sequenceNumber": seq, "started": True,
                                                "finished": True, "teams": teams}]}}}


def _event(winner: str = WIN, loser: str = LOSE, slug: str = "aslug",
           us_side: str = "long", outcome: str = "moved", t_prev: int = T_PREV,
           t_obs: int = T_OBS, map_number: int = MAP, sid=SID, lead: float | None = None) -> dict:
    ev = {"series_id": sid, "map_number": map_number, "round_number": 12,
          "winner_team": winner, "loser_team": loser, "t_prev_ns": t_prev,
          "t_obs_ns": t_obs, "outcome": outcome, "slug": slug, "us_side": us_side}
    if lead is not None:
        ev["lead_vs_obs_s"] = lead
    return ev


def _mapping(sid=SID, sides: dict | None = None) -> dict:
    us = {}
    if sides is not None:
        us["match"] = {"market_slug": MATCH_SLUG, "kind": "match", "tick": 0.01,
                       "min_qty": 0.01, "sides": sides}
    return {"type": "grid_pmus_mapping", "ts_ns": T_PREV - 3600 * NS,
            "grid_series_id": sid, "grid_teams": ["magic", "BIG"],
            "grid_start": "2026-09-03T08:00:00Z", "match": {"score": 1.0},
            "us_markets": us}


def _write_lines(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))


# --- 1. race ------------------------------------------------------------------


def test_race_edges_symmetry_and_hot_hand():
    assert vpc.race(13, 7) == 1.0
    assert vpc.race(7, 13) == 0.0
    assert vpc.race(12, 12) == pytest.approx(0.5)
    assert vpc.race(0, 0) == pytest.approx(0.5)
    assert vpc.race(12, 0) > 0.99


# --- 2. swing -----------------------------------------------------------------


def test_swing_late_round_is_more_leveraged():
    assert vpc.swing(12, 11) > vpc.swing(5, 2) > 0
    # swing(a, b) = race(a, b) - race(a - 1, b + 1)
    assert vpc.swing(5, 2) == pytest.approx(vpc.race(5, 2) - vpc.race(4, 3))


# --- 3. scores_after ----------------------------------------------------------


def test_scores_after_keying_and_ignores_non_ss():
    rows = [
        _ss(123, {WIN: 13, LOSE: 5}),
        {"type": "notss", "series_id": "A", "req_ns": 1},
        {"type": "ss", "series_id": "B", "req_ns": "bad",
         "data": {"seriesState": {"games": [{"sequenceNumber": 2, "teams": []}]}}},
        {"type": "ss", "series_id": 5, "req_ns": 9,
         "data": {"seriesState": {"games": [{"sequenceNumber": 3,
                                             "teams": [{"name": "x", "score": 1}]}]}}},
    ]
    out = vpc.scores_after(rows)
    assert out[(SID, MAP, 123)] == {WIN: 13, LOSE: 5}
    assert out[("5", 3, 9)] == {"x": 1}
    # non-ss rows and non-int req_ns produce no keys.
    assert len(out) == 2


# --- 4. tape ------------------------------------------------------------------


def test_tape_sorted_per_slug_and_dead_prices():
    rows = [
        _bbo("z", 30, 0.5, 0.6),
        _bbo("z", 10, None, 0.5),
        _bbo("z", 20, 0.0, 0.6),
        _bbo("w", 5, 0.60, 0.70),
        {"type": "other", "slug": "z", "req_ns": 1},
    ]
    t = vpc.tape(rows)
    times = [r[0] for r in t["z"]]
    assert times == sorted(times)
    # null-bid row keeps its req_ns with mid None (and bid/ask dropped as None).
    assert (10, None, None, None) in t["z"]
    # a 0.0 price is dead: no mid.
    dead = next(r for r in t["z"] if r[0] == 20)
    assert dead[1] is None
    two = next(r for r in t["z"] if r[0] == 30)
    assert two[1] == pytest.approx(0.55)
    # non-pmus_bbo and non-int req_ns rows are ignored entirely.
    assert vpc.tape([{"type": "other", "slug": "z", "req_ns": 1}]) == {}
    bad = dict(_bbo("z", 1, 0.5, 0.6), req_ns="nan")  # non-int req_ns rows are dropped
    assert vpc.tape([bad]) == {}


# --- 5. quote_at --------------------------------------------------------------


def test_quote_at_skips_dead_row_and_coverage_slack():
    # (a) last two-sided row at/before t, skipping a dead row in between.
    series_a = [(10, 0.50, 0.48, 0.52), (20, None, None, None), (30, 0.60, 0.55, 0.65)]
    q, covered = vpc.quote_at(series_a, 25)
    assert q[0] == 10 and q[1] == pytest.approx(0.50)
    assert covered is True

    # (b) covered=False when the last row is older than 35s.
    series_b = [(0, 0.5, 0.48, 0.52)]
    q, covered = vpc.quote_at(series_b, 40 * NS)
    assert q is not None and q[1] == pytest.approx(0.5)
    assert covered is False

    # (c) covered=True when a (dead) heartbeat row sits within 35s of t.
    series_c = [(0, 0.5, 0.48, 0.52), (15 * NS, None, None, None)]
    q, covered = vpc.quote_at(series_c, 30 * NS)
    assert q is not None and q[1] == pytest.approx(0.5)
    assert covered is True


# --- 6. match_side ------------------------------------------------------------


def test_match_side_orientation_and_case_insensitive():
    # winner in sides -> its side.
    assert vpc.match_side(_mapping(sides={WIN: "long"}), WIN, LOSE) == (MATCH_SLUG, "long")
    # only loser in sides -> opposite.
    assert vpc.match_side(_mapping(sides={LOSE: "short"}), WIN, LOSE) == (MATCH_SLUG, "long")
    # case-insensitive team matching ("Benched gods" vs "benched gods" happens for real).
    assert vpc.match_side(_mapping(sides={LOSE: "long"}), WIN, LOSE) == (MATCH_SLUG, "short")
    # no "match" market -> (None, None).
    assert vpc.match_side(_mapping(sides=None), WIN, LOSE) == (None, None)


# --- 7. score_events: long-side map mode --------------------------------------


def _map_long_tape():
    return vpc.tape([
        _bbo("aslug", T_PREV, 0.55, 0.65),
        _bbo("aslug", T_OBS, 0.55, 0.60),
        _bbo("aslug", T_OBS + 30 * NS, 0.62, 0.68),
    ])


def test_score_events_long_map_mode():
    ev = _event(slug="aslug", us_side="long")
    [e] = vpc.score_events([ev], SCORES, _map_long_tape(), {}, "map")
    assert e["entry_cost"] == pytest.approx(0.60)      # ask at t_obs
    assert e["spread"] == pytest.approx(0.05)
    # mid_before at t_prev = 0.60; ask at t_obs = 0.60.
    assert e["taker_30s"] == pytest.approx(0.05)       # 0.65 - 0.60
    assert e["mid_30s"] == pytest.approx(0.65 - 0.60)  # 0.65 - mid_before
    assert e["book_first"] is False                    # no lead_vs_obs_s
    # horizons with no row within 35s (gap to +120s > 35s) -> None.
    assert e["mid_120s"] is None and e["taker_120s"] is None


# --- 8. score_events: short-side orientation ----------------------------------

def _map_short_tape():
    return vpc.tape([
        _bbo("aslug", T_PREV, 0.55, 0.65),
        _bbo("aslug", T_OBS, 0.70, 0.72),
        _bbo("aslug", T_OBS + 30 * NS, 0.60, 0.70),
    ])


def test_score_events_short_map_mode():
    ev = _event(slug="aslug", us_side="short")
    [e] = vpc.score_events([ev], SCORES, _map_short_tape(), {}, "map")
    assert e["entry_cost"] == pytest.approx(1 - 0.70)  # 1 - bid at t_obs
    assert e["taker_30s"] == pytest.approx((1 - 0.65) - (1 - 0.70))
    assert e["mid_30s"] == pytest.approx(-1 * (0.65 - 0.60))  # sign -1 orientation


# --- 9. score_events: match mode ----------------------------------------------

def _match_tape():
    return vpc.tape([
        _bbo(MATCH_SLUG, T_PREV, 0.55, 0.65),
        _bbo(MATCH_SLUG, T_OBS, 0.70, 0.80),
        _bbo(MATCH_SLUG, T_OBS + 30 * NS, 0.74, 0.84),
    ])


def test_score_events_match_mode_replaces_slug_and_drops_unmapped():
    mapped = _event(slug="ignored-slug", us_side="ignored")  # replaced by mapping.
    unmapped = _event(sid="NOMAP")                            # no match market -> dropped.
    events = [mapped, unmapped]
    mappings = {SID: _mapping(sides={WIN: "long"})}
    out = vpc.score_events(events, SCORES, _match_tape(), mappings, "match")
    assert [e["slug"] for e in out] == [MATCH_SLUG]
    assert out[0]["us_side"] == "long"
    assert all(e["round_number"] == 12 for e in out) and len(out) == 1


# --- 10. score_events: skip rules ---------------------------------------------

def _skip_tape():
    return vpc.tape([
        _bbo("aslug", T_PREV, 0.55, 0.65),
        _bbo("aslug", T_OBS, 0.55, 0.60),
        _bbo("aslug", T_OBS + 30 * NS, 0.62, 0.68),
        _bbo("deadslug", T_OBS, None, 0.0),   # present but never two-sided.
    ])


def test_score_events_skips():
    events = [
        _event(slug="aslug", outcome="skipped:no_book"),            # bad outcome.
        _event(slug="aslug", t_obs=T_OBS + 5 * NS),                 # score missing.
        _event(slug="nostore"),                                     # slug absent from tape.
        _event(slug="deadslug"),                                    # no two-sided quote.
        _event(slug="aslug"),                                       # survives.
    ]
    out = vpc.score_events(events, SCORES, _skip_tape(), {}, "map")
    assert [e["round_number"] for e in out] == [12]


# --- 11. render ---------------------------------------------------------------

def _render_row(sid: str, swing: float, mid: float = 0.02) -> dict:
    return {"series_id": sid, "swing": swing, "spread": 0.02, "book_first": False,
            "mid_30s": mid, "mid_60s": mid, "mid_120s": mid,
            "taker_30s": mid, "taker_60s": mid, "taker_120s": mid, "taker_300s": mid}


def test_render_buckets_and_counts():
    rows = [_render_row(f"S{i}", 0.01) for i in (1, 2)]       # swing<0.05, n=2
    rows += [_render_row(f"S{i}", 0.5, mid=0.02) for i in (3, 4, 5)]  # >=0.20, n=3
    out = vpc.render(DAY, "match", rows, n_events=6)
    assert "# venue pricing cut — 20260903 — MATCH book" in out
    assert "6 book-joined round ends" in out
    assert "== swing<0.05: n=2 series=2" in out       # small bucket prints n=<k>
    assert "== >=0.20: n=3 series=3" in out
    assert "0.05-0.10" not in out                     # empty bucket omitted
    # the >=0.20 bucket (3+ pairs) renders a bootstrapped block, not n=<k>.
    assert " n=3 " in out


# --- 12. spread_lines ---------------------------------------------------------

def test_spread_lines_dead_share_and_ordering():
    rows = [
        _bbo("s1", 1, 0.70, 0.72, kind="match"),
        _bbo("s2", 2, 0.71, 0.75, kind="match"),
        _bbo("s3", 3, None, 0.5, kind="match"),
        _bbo("s4", 4, 0.50, 0.55, kind="map"),
        _bbo("s5", 5, 0.0, 0.5, kind="map"),
    ]
    lines = vpc.spread_lines(rows)
    assert lines[0] == "# spread by book kind (whole tape)"
    kinds = [ln.split(":")[0] for ln in lines[1:]]
    assert kinds == ["map", "match"]                  # sorted
    map_ln = next(ln for ln in lines if ln.startswith("map:"))
    match_ln = next(ln for ln in lines if ln.startswith("match:"))
    assert "rows=2 dead/one-sided=1 (50%)" in map_ln and "p50=0.05" in map_ln
    assert "rows=3 dead/one-sided=1 (33%)" in match_ln and "p50=0.04" in match_ln


# --- 13. main ----------------------------------------------------------------

def _write_day(data: Path) -> None:
    _write_lines(data / "pmus" / f"books-{DAY}.jsonl", [
        _bbo(MATCH_SLUG, T_PREV, 0.55, 0.65),
        _bbo(MATCH_SLUG, T_OBS, 0.55, 0.60),
        _bbo(MATCH_SLUG, T_OBS + 30 * NS, 0.62, 0.68),
    ])
    _write_lines(data / "grid" / f"series_state-{DAY}.jsonl", [_ss(T_OBS, {WIN: 12, LOSE: 10})])
    _write_lines(data / "pmus" / f"mappings-{DAY}.jsonl", [_mapping(sides={WIN: "long"})])
    (data / "analysis" / f"timing_chain-{DAY}.json").parent.mkdir(parents=True, exist_ok=True)
    (data / "analysis" / f"timing_chain-{DAY}.json").write_text(json.dumps({
        "events": [_event(slug=MATCH_SLUG, us_side="long")]}))


def test_main_renders_day(tmp_path, capsys):
    _write_day(tmp_path)
    assert vpc.main([DAY], data=tmp_path) == 0
    out = capsys.readouterr().out
    assert "# venue pricing cut — 20260903 — MAP book" in out


def test_main_missing_timing_chain_exits(tmp_path, capsys):
    assert vpc.main(["20260904"], data=tmp_path) == 1
    assert "no timing chain JSON" in capsys.readouterr().err