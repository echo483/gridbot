"""Tests for scripts.roundwin2_poolb (Pool B drift-first, describe-only).

Analysis only (slow loop, iron rail 1): locks in the Pool B join semantics per
docs/superpowers/specs/2026-08-28-roundwin-v2-design.md §2.2 — the mapentry ↔
postquote key (naming trap: market_slug vs slug), same-book orientation
isolation (§2.3.3), the strictly-earlier as-of rule (§2.3.1), the inherited F1
leak filter on round-level rows, the engine's fail-closed drift gate as the
entry rule, the winner-file-only settlement, and the series-clustered
bootstrap.

ENGINE-FAITHFUL DEDUP ORDER (review fix 2026-08-31): the live engine consumes
its dedup slot ONLY on gate-pass placement (engine.py: self.seen.add runs on
the placement path alone; every skip returns before it). These tests pin that
order: the drift gate evaluates the FULL candidate population, drift-fail
crossings keep the key live, and a LATER confirmed crossing of the same book
enters. The prior first-row-wins pre-gate dedup made the gate unsatisfiable
and is gone. Slate rows (engine slate gate, recording-only) are excluded like
the engine excludes them: without consuming the key.

No network, no keyring, no repo data — every input is a synthetic dict built
inline; file behaviour is exercised through tmp_path CLI subprocess runs
(mirroring test_entry_quality.py).
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure the project root is on sys.path so `scripts` is importable (mirror
# the existing test_entry_quality.py).
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from scripts import roundwin2_poolb as pb

NS = 1_000_000_000
SLUG = "astatc-cs2-va-5s-2026-08-27-map1"
VA_WIN = {"winner": "VA", "loser": "5s"}


# ── synthetic-input builders ────────────────────────────────────────────────

def mk_mapentry(ts_ns=NS, series_id="s1", map_number=1, round_number=2,
                team="VA", opponent="5s", a=1, b=0, p_map=0.7803,
                market_slug=SLUG, us_side="long", bid=0.55, ask=0.58,
                side_ask=0.58, entry_price_max=0.7403, qualifies=True,
                **kw) -> dict:
    """A qualifying mapentry row shaped like the live capture producer."""
    row = {
        "type": "mapentry",
        "series_id": series_id,
        "map_number": map_number,
        "round_number": round_number,
        "team": team,
        "opponent": opponent,
        "a": a,
        "b": b,
        "q_round": 0.71,
        "p_map": p_map,
        "dedup_extra": "ct:0.65",
        "market_slug": market_slug,
        "us_side": us_side,
        "bid": bid,
        "ask": ask,
        "side_ask": side_ask,
        "entry_price_max": entry_price_max,
        "qualifies": qualifies,
        "src_ts_ns": ts_ns,
        "ts_ns": ts_ns,
    }
    row.update(kw)
    return row

def mk_postquote(ts_ns, move, series_id="s1", map_number=1, slug=SLUG,
                 us_side="long", dt_s=120.0, **kw) -> dict:
    """A postquote drift sample at the 120s horizon."""
    row = {
        "type": "postquote",
        "slug": slug,
        "us_side": us_side,
        "mid0": 0.5,
        "src_ts_ns": ts_ns,
        "series_id": series_id,
        "map_number": map_number,
        "dedup_extra": "ct:0.65",
        "dt_s": dt_s,
        "bid": 0.49,
        "ask": 0.51,
        "mid": 0.5,
        "oriented_move": move,
        "ts_ns": ts_ns,
    }
    row.update(kw)
    return row

def mk_winners(*specs) -> dict[str, dict]:
    """A grid_winners --out winners mapping from (series, map, winner) specs."""
    return {f"{s}:{m}": {"winner": w, "loser": l}
            for s, m, w, l in specs}


def _drift_history(n=3) -> dict:
    """A (slug, long) book with n confirming prior 120s samples at ts 1..n s."""
    return pb.book_history([mk_postquote(i * NS, 0.05) for i in range(1, n + 1)])


# ── (a) F1 leak filter on round-level rows ──────────────────────────────────

def test_f1_filter_accepts_coherent_round_row():
    # a+b = 1 round played, round_number 2: coherent (1 + 1 == 2).
    assert pb.f1_leak_ok(mk_mapentry(a=1, b=0, round_number=2)) is True

def test_f1_filter_drops_incoherent_round_row():
    # a+b = 6 rounds played but round_number says 5: rounds_played+1 != rn.
    assert pb.f1_leak_ok(mk_mapentry(a=3, b=3, round_number=5)) is False

def test_f1_filter_prefers_features_rounds_played_when_carried():
    # A row-level feature row: features.rounds_played is authoritative even
    # when the a/b slots disagree (feature fields are the leak channel).
    row = mk_mapentry(a=1, b=0, round_number=2)
    row["features"] = {"rounds_played": 6.0}
    assert pb.f1_leak_ok(row) is False
    row["features"] = {"rounds_played": 1.0}
    assert pb.f1_leak_ok(row) is True

def test_f1_filter_needs_the_round_number_leg_too():
    # BOTH legs of the assertion must agree: a score-coherent row whose
    # round_number leg is missing/non-numeric cannot pass (review fix 3).
    assert pb.f1_leak_ok(mk_mapentry(a=1, b=0, round_number="2")) is False
    assert pb.f1_leak_ok(mk_mapentry(a=1, b=0, round_number=None)) is False
    row = mk_mapentry(a=1, b=0, round_number=2)
    row["features"] = {"rounds_played": "1"}     # non-numeric leg -> drop
    assert pb.f1_leak_ok(row) is False
    row["features"] = {"rounds_played": True}    # bool leg -> drop
    assert pb.f1_leak_ok(row) is False

def test_f1_filter_fails_closed_when_un_evaluable():
    # No scores, no features: the assertion cannot be evaluated -> drop
    # (§2.3.4 drops rows where the assertion cannot be evaluated).
    assert pb.f1_leak_ok(mk_mapentry(a=None, b=None)) is False
    assert pb.f1_leak_ok({}) is False
    assert pb.f1_leak_ok(mk_mapentry(a="3", b=0)) is False     # non-numeric
    assert pb.f1_leak_ok(mk_mapentry(round_number=None)) is False


# ── (b) join semantics: keys, naming trap, orientation isolation ────────────

def test_book_history_groups_by_slug_and_side():
    hist = pb.book_history([
        mk_postquote(1 * NS, 0.02),
        mk_postquote(2 * NS, 0.03, slug=SLUG + "b"),
        mk_postquote(3 * NS, -0.05, us_side="short"),
    ])
    assert set(hist) == {
        (SLUG, "long"), (SLUG + "b", "long"), (SLUG, "short")}

def test_short_book_never_sees_long_books_moves():
    # Same slug, opposite side: the SHORT book's evidence must NOT include the
    # LONG book's oriented moves (never pool unoriented across sides).
    hist = pb.book_history([mk_postquote(1 * NS, 0.10, us_side="long")])
    moves = pb.prior_drift_moves(hist, SLUG, "short", 10 * NS)
    assert moves == []

def test_other_slug_same_series_is_not_evidence():
    # Same series and map but a different book: not evidence for this book.
    hist = pb.book_history([mk_postquote(1 * NS, 0.10, slug=SLUG + "other")])
    assert pb.prior_drift_moves(hist, SLUG, "long", 10 * NS) == []

def test_book_history_horizon_tolerance():
    # Engine tolerance: |dt_s - 120| <= 1.0 counts; 30/60s never do, and
    # three views of one moment can never masquerade as three confirmations.
    hist = pb.book_history([
        mk_postquote(1 * NS, 0.99, dt_s=30.0),
        mk_postquote(2 * NS, 0.99, dt_s=60.0),
        mk_postquote(3 * NS, 0.99, dt_s=119.0),
        mk_postquote(4 * NS, 0.99, dt_s=121.0),
        mk_postquote(5 * NS, 0.02, dt_s=120.0),
    ])
    assert hist[(SLUG, "long")] == [(3 * NS, 0.99), (4 * NS, 0.99),
                                    (5 * NS, 0.02)]

def test_book_history_garbage_rows_never_crash():
    # Security leftovers: a float() overflow on dt_s and a ts_ns too large to
    # int() are skipped, never raised (OverflowError in the excepts).
    hist = pb.book_history([
        mk_postquote(1 * NS, 0.05, dt_s=10 ** 400),   # float() OverflowError
        mk_postquote(1e400, 0.05),                    # int() OverflowError
        mk_postquote(3 * NS, 0.05),
    ])
    assert hist[(SLUG, "long")] == [(3 * NS, 0.05)]

def test_prior_moves_visible_by_write_time_strictly_before():
    # Rows are visible by their WRITE time (ts_ns), strictly before the
    # decision (§2.3.1/§2.3.2): a row written at t is not visible at t, and
    # rows written after t are invisible even when physically present.
    hist = pb.book_history([
        mk_postquote(1 * NS, 0.01),
        mk_postquote(5 * NS, 0.02),      # written exactly at t: excluded
        mk_postquote(9 * NS, 0.03),      # written after t: excluded
    ])
    moves = pb.prior_drift_moves(hist, SLUG, "long", 5 * NS)
    assert moves == [0.01]

def test_entry_own_outcome_is_never_its_own_evidence():
    # The eval's own 120s sample materializes ~120s AFTER the decision, so it
    # cannot confirm the drift it would later realize (label separation,
    # §2.3.2 write-time rule).
    hist = pb.book_history([
        mk_postquote(1 * NS, 0.05),
        mk_postquote(2 * NS, 0.05),
        mk_postquote(9 * NS, 0.50),      # the deciding eval's own outcome
    ])
    moves = pb.prior_drift_moves(hist, SLUG, "long", 5 * NS)
    assert moves == [0.05, 0.05]
    entries = pb.apply_drift_gate(
        [mk_mapentry(ts_ns=5 * NS)], hist)
    assert entries[0]["drift_confirmed"] is False

def test_history_older_than_file_window_invisible():
    # The engine reads today + yesterday files only: rows written >2 days
    # before the decision are out of window.
    hist = pb.book_history([mk_postquote(1 * NS, 0.05)])
    far = 1 * NS + 3 * 86400 * NS
    assert pb.prior_drift_moves(hist, SLUG, "long", far) == []

def test_prior_moves_sorted_and_windowed_most_recent_last():
    # Rows arrive in append order out of chronological order; the evidence is
    # time-sorted, most recent LAST, capped at the engine window (3).
    rows = [mk_postquote(9 * NS, 0.03), mk_postquote(1 * NS, 0.01),
            mk_postquote(5 * NS, 0.02), mk_postquote(0, 0.00)]
    hist = pb.book_history(rows)
    moves = pb.prior_drift_moves(hist, SLUG, "long", 100 * NS)
    assert moves == [0.01, 0.02, 0.03]   # oldest 0.00 slid out of the window

def test_mapentry_postquote_slug_naming_join():
    # Naming trap: mapentry carries `market_slug`, postquote carries `slug`.
    # select_poolb_entries normalizes the entry's slug so the join uses the
    # postquote name.
    me = mk_mapentry(ts_ns=9 * NS)
    entries, counters = pb.select_poolb_entries([me])
    assert counters["candidates"] == 1
    assert entries[0]["slug"] == SLUG
    assert entries[0]["market_slug"] == SLUG
    hist = pb.book_history([mk_postquote(1 * NS, 0.05),
                            mk_postquote(2 * NS, 0.05),
                            mk_postquote(3 * NS, 0.05)])
    entries = pb.apply_drift_gate(entries, hist)
    assert entries[0]["drift_confirmed"] is True


# ── (c) candidate gates, slate exclusion, engine-faithful dedup ─────────────

def test_non_qualifying_and_bookless_rows_gated_out():
    rows = [
        mk_mapentry(qualifies=False),
        mk_mapentry(market_slug=None),                 # no PMUS mapping
        mk_mapentry(us_side=None),
        mk_mapentry(bid=None),                          # zero-BBO dead book
    ]
    _, counters = pb.select_poolb_entries(rows)
    assert counters["not_qualifying"] == 1
    assert counters["no_book"] == 3
    assert counters["candidates"] == 0

def test_slate_rows_excluded_like_engine_gate():
    # Queue-16 slate rows are recording-only; the engine never trades them
    # (engine.py slate gate on src_hypothesis_id). The replay must exclude
    # them exactly the same way.
    rows = [
        mk_mapentry(ts_ns=1 * NS, src_hypothesis_id="H-MAPENTRY1-SLATE"),
        mk_mapentry(ts_ns=2 * NS),                      # detector row
    ]
    entries, counters = pb.select_poolb_entries(rows)
    assert counters["slate_row"] == 1
    assert len(entries) == 1 and entries[0]["ts_ns"] == 2 * NS

def test_slate_row_never_consumes_the_dedup_key():
    # The engine slate gate is record-only and returns BEFORE seen.add: a
    # slate row on a key must not block the real detector crossing on the
    # same key. Here the slate row is earlier and shares the key.
    slate = mk_mapentry(ts_ns=1 * NS, round_number=1, a=0, b=0,
                        src_hypothesis_id="H-MAPENTRY1-SLATE")
    real = mk_mapentry(ts_ns=9 * NS)
    candidates, counters = pb.select_poolb_entries([slate, real])
    assert counters["slate_row"] == 1 and len(candidates) == 1
    placed, placement = pb.place_with_dedup(
        pb.apply_drift_gate(candidates, _drift_history()))
    assert placement["placed"] == 1 and placed[0]["ts_ns"] == 9 * NS

def test_f1_filtered_row_does_not_consume_dedup_key():
    # The leak filter gates the BASE row, before any dedup accounting: a
    # leaky first row must not block the later coherent row for the same book
    # (engine-faithful: nothing skipped pre-placement consumes the key).
    rows = [
        mk_mapentry(ts_ns=1 * NS, a=3, b=3, round_number=5),  # leaky: dropped
        mk_mapentry(ts_ns=2 * NS, a=1, b=0, round_number=2),  # coherent: kept
    ]
    entries, counters = pb.select_poolb_entries(rows)
    assert counters["f1_leak_filtered"] == 1
    assert len(entries) == 1 and entries[0]["ts_ns"] == 2 * NS

def test_engine_dedup_consumed_only_on_gate_pass_placement():
    # THE engine-faithful order pin (review fix 2026-08-31): the drift gate
    # evaluates the FULL candidate population; the dedup slot is consumed ONLY
    # when a crossing finally PLACES (engine.py: self.seen.add on the
    # placement path alone). The first crossing has no history and fails the
    # gate — under the old first-row-wins pre-gate dedup that dead row was
    # the book's ONLY candidate and the gate was unsatisfiable. Now the
    # drift-fail keeps the key live, so the later confirmed crossing enters.
    early = mk_mapentry(ts_ns=1 * NS)                       # no history yet
    late = mk_mapentry(ts_ns=9 * NS, round_number=8, a=7, b=0)  # same book
    hist = _drift_history()                                 # samples at 1..3s
    candidates, _ = pb.select_poolb_entries([early, late])
    candidates = pb.apply_drift_gate(candidates, hist)
    assert candidates[0]["drift_confirmed"] is False        # fail-closed
    assert candidates[1]["drift_confirmed"] is True         # history built
    placed, placement = pb.place_with_dedup(candidates)
    assert placement["no_drift_confirm"] == 1
    assert placement["placed"] == 1
    assert placed[0]["ts_ns"] == 9 * NS                     # LATER crossing enters

def test_later_confirmed_crossings_do_not_average_in():
    # Once a gate-passing crossing consumes the slot, subsequent confirmed
    # crossings of the same book are counted, never placed (ONE entry per
    # book, exactly like the engine).
    rows = [
        mk_mapentry(ts_ns=9 * NS),
        mk_mapentry(ts_ns=10 * NS, round_number=3, a=2, b=0),
    ]
    candidates = pb.apply_drift_gate(
        pb.select_poolb_entries(rows)[0], _drift_history())
    placed, placement = pb.place_with_dedup(candidates)
    assert [p["ts_ns"] for p in placed] == [9 * NS]
    assert placement["duplicate_key_after_gate"] == 1
    assert placement["placed"] == 1

def test_placement_band_gate_is_record_only():
    # Out-of-band candidates fail BEFORE the dedup slot is touched and never
    # consume it (engine band gate: record-only skip).
    rows = [mk_mapentry(ts_ns=9 * NS, p_map=0.95)]          # outside (0.50, 0.80)
    candidates = pb.apply_drift_gate(
        pb.select_poolb_entries(rows)[0], _drift_history())
    assert candidates[0]["drift_confirmed"] is True
    placed, placement = pb.place_with_dedup(candidates)
    assert placed == []
    assert placement["band_gated"] == 1 and placement["placed"] == 0

def test_missing_ts_ns_entry_fails_closed_not_crash():
    # A candidate with no ts_ns has no decision time: fail closed (empty
    # evidence), never crash, never place — and it must not consume a slot.
    e = mk_mapentry()
    e.pop("ts_ns")
    e.pop("src_ts_ns")
    entries = pb.apply_drift_gate([e], _drift_history())
    assert entries[0]["drift_moves"] == []
    assert entries[0]["drift_confirmed"] is False
    placed, placement = pb.place_with_dedup(entries)
    assert placed == [] and placement["no_drift_confirm"] == 1

def test_entry_price_follows_frozen_rule():
    rows = [mk_mapentry(side_ask=0.58, entry_price_max=0.7403)]
    entries, _ = pb.select_poolb_entries(rows)
    assert entries[0]["entry_price"] == 0.57   # min(0.58-0.01, 0.7403, 0.90)

def test_malformed_price_rows_counted_never_crash():
    rows = [
        mk_mapentry(side_ask=None),
        mk_mapentry(entry_price_max=None),
        {**mk_mapentry(), "side_ask": "high"},
        {**mk_mapentry(), "entry_price_max": 10 ** 400},
    ]
    _, counters = pb.select_poolb_entries(rows)
    assert counters["candidates"] == 0
    assert counters["entry_price_out_of_range"] + counters["malformed_row"] == 4


# ── (d) drift-first entry rule: the engine gate, fail-closed ────────────────

def test_drift_gate_requires_full_window():
    # Fewer than DRIFT_WINDOW samples: fail closed (review F5 — a partial
    # window could otherwise confirm on ONE sample).
    hist = pb.book_history([mk_postquote(1 * NS, 0.05)])
    entries = pb.apply_drift_gate(
        [mk_mapentry(ts_ns=9 * NS)], hist)
    assert entries[0]["drift_confirmed"] is False

def test_drift_gate_mean_threshold_boundary():
    # mean == DRIFT_MIN_CONFIRM exactly: >= passes (the engine's frozen bar).
    hist = pb.book_history([mk_postquote(i * NS, pb.DRIFT_MIN_CONFIRM)
                            for i in (1, 2, 3)])
    entries = pb.apply_drift_gate([mk_mapentry(ts_ns=9 * NS)], hist)
    assert entries[0]["drift_confirmed"] is True

def test_drift_gate_fails_when_mean_below_bar():
    # Three 0.2pt moves sum to 0.6pt but mean 0.002 < 0.01: NOT confirmed
    # (the sum-vs-mean review F5 trap).
    hist = pb.book_history([mk_postquote(i * NS, 0.002) for i in (1, 2, 3)])
    entries = pb.apply_drift_gate([mk_mapentry(ts_ns=9 * NS)], hist)
    assert entries[0]["drift_confirmed"] is False

def test_drift_gate_fails_closed_on_non_finite_move():
    # A bare-JSON Infinity in the history: the engine filters non-finite moves
    # out, so only 2 usable samples remain -> below the window -> fail closed.
    hist = pb.book_history([
        mk_postquote(1 * NS, float("inf")),
        mk_postquote(2 * NS, 0.05),
        mk_postquote(3 * NS, 0.05),
    ])
    entries = pb.apply_drift_gate([mk_mapentry(ts_ns=9 * NS)], hist)
    assert len(entries[0]["drift_moves"]) == 2
    assert entries[0]["drift_confirmed"] is False

def test_drift_gate_uses_only_prior_samples_of_entry():
    # Evidence snapshots: only moves strictly before the entry's src_ts_ns,
    # from the entry's own book, enter the verdict.
    hist = pb.book_history([
        mk_postquote(1 * NS, 0.05),
        mk_postquote(2 * NS, 0.05),
        mk_postquote(3 * NS, 0.05),
        mk_postquote(9 * NS, 0.05),    # AFTER the entry moment
    ])
    entries = pb.apply_drift_gate([mk_mapentry(ts_ns=5 * NS)], hist)
    assert entries[0]["drift_moves"] == [0.05, 0.05, 0.05]
    assert entries[0]["drift_confirmed"] is True
    # A LATER entry on the same book sees all four (window-capped to 3).
    late = pb.apply_drift_gate(
        [mk_mapentry(ts_ns=10 * NS, market_slug=SLUG, round_number=3,
                     a=2, b=0)], hist)
    assert len(late[0]["drift_moves"]) == 3   # window cap, most recent 3

def test_drift_gate_missing_history_is_false_not_crash():
    entries = pb.apply_drift_gate([mk_mapentry()], {})
    assert entries[0]["drift_confirmed"] is False
    assert entries[0]["drift_moves"] == []


# ── (e) settlement: winner-file-only, exact names, edge ─────────────────────

def test_settle_won_and_lost_by_exact_name():
    entries, _ = pb.select_poolb_entries([
        mk_mapentry(ts_ns=1 * NS, series_id="s1"),
        mk_mapentry(ts_ns=2 * NS, series_id="s2", market_slug=SLUG + "2"),
    ])
    winners = mk_winners(("s1", 1, "VA", "5s"),     # row1's team won
                         ("s2", 1, "5s", "VA"))     # row1's team lost
    settled, excluded = pb.settle_entries(entries, winners)
    assert excluded == {"no_grid_verdict": 0, "team_not_in_verdict": 0}
    assert [s["won"] for s in settled] == [1.0, 0.0]
    assert settled[0]["edge"] == pytest.approx(1.0 - 0.57)
    assert settled[1]["edge"] == pytest.approx(-0.57)

def test_settle_missing_verdict_excluded_but_counted():
    entries, _ = pb.select_poolb_entries([mk_mapentry()])
    settled, excluded = pb.settle_entries(entries, {})
    assert settled == []
    assert excluded["no_grid_verdict"] == 1

def test_settle_team_not_in_verdict_never_guessed():
    entries, _ = pb.select_poolb_entries([mk_mapentry()])
    settled, excluded = pb.settle_entries(
        entries, mk_winners(("s1", 1, "Alpha", "Beta")))
    assert settled == []
    assert excluded["team_not_in_verdict"] == 1

def test_settle_partial_verdict_entry_is_unusable():
    # A malformed winners entry (no winner) is never scored.
    entries, _ = pb.select_poolb_entries([mk_mapentry()])
    settled, excluded = pb.settle_entries(
        entries, {"s1:1": {"winner": None, "loser": "5s"}})
    assert settled == []
    assert excluded["no_grid_verdict"] == 1


# ── (f) metrics: describe-only shape + series-clustered bootstrap ───────────

def test_metrics_empty_is_null_not_zero():
    m = pb._metrics([])
    assert m == {"n": 0, "win_rate": None, "mean_edge": None,
                 "total_edge": None}

def test_bootstrap_is_series_clustered_and_seeded():
    # Two series with opposite outcomes: cluster resampling must sometimes
    # produce all-win and all-lose draws (clusters move together), which a
    # per-row bootstrap could not produce.
    settled = [
        {"series_id": "sA", "won": 1.0, "edge": 0.43},
        {"series_id": "sA", "won": 1.0, "edge": 0.30},
        {"series_id": "sB", "won": 0.0, "edge": -0.57},
        {"series_id": "sB", "won": 0.0, "edge": -0.57},
    ]
    boot = pb.series_cluster_bootstrap(settled, b=200, seed=42)
    assert boot["clusters"] == 2
    lo, hi = boot["win_rate_ci"]
    assert lo == 0.0 and hi == 1.0            # both pure-cluster draws occurred
    # Determinism: same seed -> identical interval.
    again = pb.series_cluster_bootstrap(settled, b=200, seed=42)
    assert again == boot

def test_bootstrap_empty_input_is_null():
    assert pb.series_cluster_bootstrap([])["win_rate_ci"] is None

def test_day_of_overflow_and_garbage_is_unknown():
    # Security leftovers: OverflowError is in the excepts — a ts beyond
    # float range or datetime range never raises.
    assert pb.day_of({"src_ts_ns": 10 ** 400}) == "unknown"
    assert pb.day_of({"src_ts_ns": float("inf")}) == "unknown"
    assert pb.day_of({"src_ts_ns": "garbage"}) == "unknown"
    assert pb.day_of({}) == "unknown"


# ── (g) atomic doc write ─────────────────────────────────────────────────────

def test_write_doc_atomic_lands_and_leaves_no_tmp(tmp_path):
    p = tmp_path / "sub" / "doc.md"
    pb.write_doc_atomic(str(p), "hello")
    assert p.read_text(encoding="utf-8") == "hello"
    assert not list((tmp_path / "sub").glob("*.tmp.*"))


# ── (h) CLI: end-to-end on tmp files, stdout JSON, one stderr line ──────────

def _write_inputs(tmp_path):
    pq_rows = [
        # s1 book with three confirming prior 120s samples -> gated entry, win.
        *[mk_postquote(i * NS, 0.05) for i in (1, 2, 3)],
        # s2 same drift but the map winner favours the opponent -> gated loss.
        *[mk_postquote(i * NS, 0.05, series_id="s2", slug=SLUG + "2")
          for i in (1, 2, 3)],
        # s3 confirming drift on the LONG side only; the SHORT book must not
        # see it (orientation isolation) -> not confirmed.
        *[mk_postquote(i * NS, 0.05, series_id="s3", slug=SLUG + "3")
          for i in (1, 2, 3)],
    ]
    me_rows = [
        mk_mapentry(ts_ns=9 * NS, series_id="s1"),
        mk_mapentry(ts_ns=9 * NS, series_id="s2", market_slug=SLUG + "2"),
        mk_mapentry(ts_ns=9 * NS, series_id="s3", market_slug=SLUG + "3",
                    us_side="short", side_ask=0.42, entry_price_max=0.46),
        # s4 leaky round row -> F1 filtered before anything else sees it.
        mk_mapentry(ts_ns=9 * NS, series_id="s4", market_slug=SLUG + "4",
                    a=3, b=3, round_number=5),
        # s5 slate row -> engine slate gate exclusion (recording-only).
        mk_mapentry(ts_ns=9 * NS, series_id="s5", market_slug=SLUG + "5",
                    src_hypothesis_id="H-MAPENTRY1-SLATE"),
    ]
    me = tmp_path / "mapentry-test.jsonl"
    pq = tmp_path / "postquote-test.jsonl"
    me.write_text("".join(json.dumps(r) + "\n" for r in me_rows),
                  encoding="utf-8")
    pq.write_text("".join(json.dumps(r) + "\n" for r in pq_rows),
                  encoding="utf-8")
    winners = tmp_path / "winners.json"
    winners.write_text(json.dumps({"winners": mk_winners(
        ("s1", 1, "VA", "5s"), ("s2", 1, "5s", "VA"))}), encoding="utf-8")
    return str(me), str(pq), str(winners)


def _run_cli(tmp_path, *extra):
    return subprocess.run(
        [sys.executable, "-X", "utf8",
         str(_project_root / "scripts" / "roundwin2_poolb.py"),
         "--no-reconcile", *extra],
        capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(_project_root)},
        cwd=str(tmp_path),
    )


def test_cli_end_to_end_stdout_stderr_and_doc(tmp_path):
    me, pq, winners = _write_inputs(tmp_path)
    out_doc = tmp_path / "report.md"
    proc = _run_cli(tmp_path, "--winners", winners, "--out-doc", str(out_doc),
                    "--bootstrap", "100", "--days", f"{me},{pq}")
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["funnel"]["rows_loaded"] == 5
    assert doc["funnel"]["f1_leak_filtered"] == 1
    assert doc["funnel"]["slate_row"] == 1
    assert doc["funnel"]["candidates"] == 3
    # Placement pass: s1 + s2 confirm; s3 short has no evidence.
    assert doc["placement"]["placed"] == 2
    assert doc["placement"]["no_drift_confirm"] == 1
    assert doc["placement"]["duplicate_key_after_gate"] == 0
    assert doc["gated"]["n"] == 2
    assert doc["gated"]["metrics"]["n"] == 2
    assert doc["gated"]["metrics"]["win_rate"] == pytest.approx(0.5)
    assert doc["gated"]["metrics"]["mean_edge"] == pytest.approx(
        ((1.0 - 0.57) + (-0.57)) / 2)
    # Ungated contrast sees all three candidates (no drift gate, no dedup).
    assert doc["ungated"]["n"] == 3
    # ONE-line stderr summary contract.
    err = [ln for ln in proc.stderr.splitlines() if ln.strip()]
    assert len(err) == 1 and err[0].startswith("roundwin2_poolb:")
    assert "candidates=3" in err[0] and "f1_filtered=1" in err[0]
    # Markdown report written atomically and headline sections present.
    text = out_doc.read_text(encoding="utf-8")
    assert "Pool B" in text and "Headline metrics" in text
    assert "F1 leak filtered" in text and "Describe-only" in text
    assert "slate rows excluded" in text
    assert not list(tmp_path.glob("*.tmp.*"))

def test_cli_days_filter_selects_files(tmp_path):
    me, pq, winners = _write_inputs(tmp_path)
    proc = _run_cli(tmp_path, "--winners", winners, "--days", "20990101",
                    "--out-doc", str(tmp_path / "empty.md"),
                    "--bootstrap", "10")
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["funnel"]["rows_loaded"] == 0
    assert doc["gated"]["metrics"]["win_rate"] is None

def test_cli_winners_missing_file_is_a_clean_error(tmp_path):
    proc = _run_cli(tmp_path, "--winners", str(tmp_path / "nope.json"))
    assert proc.returncode == 2
    assert "not found" in proc.stderr
    assert not proc.stdout.strip()          # no JSON doc emitted

def test_cli_bootstrap_negative_rejected(tmp_path):
    me, pq, winners = _write_inputs(tmp_path)
    proc = _run_cli(tmp_path, "--winners", winners, "--bootstrap", "-1",
                    "--days", f"{me},{pq}")
    assert proc.returncode == 2
    assert "--bootstrap must be >= 0" in proc.stderr

def test_cli_bootstrap_zero_accepted(tmp_path):
    me, pq, winners = _write_inputs(tmp_path)
    proc = _run_cli(tmp_path, "--winners", winners, "--bootstrap", "0",
                    "--days", f"{me},{pq}",
                    "--out-doc", str(tmp_path / "z.md"))
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["params"]["bootstrap_b"] == 0

def test_postquote_overflow_move_is_not_evidence(tmp_path):
    """Review D: a 10**400 oriented_move must fail _finite_num (routed), not
    crash or leak into the drift evidence histogram."""
    me, pq, winners = _write_inputs(tmp_path)
    slug = None
    pre_count = 0
    for line in open(pq):
        r = json.loads(line)
        if r.get("type") == "postquote":
            if slug is None:
                slug, side, move, ts = r["slug"], r["us_side"], r["oriented_move"], r["ts_ns"]
            if r["slug"] == slug and r["us_side"] == side:
                pre_count += 1
    if slug is None:
        raise AssertionError("no postquote row in fixture")
    with open(pq, "a") as f:
        f.write(json.dumps({
            "type": "postquote", "ts_ns": ts + 1, "slug": slug,
            "us_side": side, "dt_s": 120, "oriented_move": 10**400,
        }) + "\n")
    rows = [json.loads(l) for l in open(pq)]
    hist = pb.book_history(rows, horizon=120)
    entry = hist.get((slug, side), [])
    assert len(entry) == pre_count      # overflow row excluded, originals kept
    assert all(m != 10**400 for _, m in entry)

def test_no_real_data_needed_for_import():
    # The module imports without touching the keyring or network and exposes
    # the pure surface the tests and the analysis lane rely on.
    for name in pb.__all__:
        assert callable(getattr(pb, name)), name
    assert math.isfinite(pb.DRIFT_MIN_CONFIRM) and pb.DRIFT_WINDOW >= 1
