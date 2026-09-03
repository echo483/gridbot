"""Hermetic tests for scripts/postshock_fade.py (describe-only backtest).

Synthetic rows only — no files, no network.  Pins the RECONCILED 2026-09-03
funnel (docs/analysis/2026-09-01-postshock-anchor-trace.md §3 reference) and
the REVIEW diagnostics so a future edit cannot silently change the population.

Each test builds mapentry-shaped sequence rows and detect()-shaped shock
records directly.  The SeqIndex is built through the real sequences_by_key()
exactly as run_entries does (synthetic rows are un-tagged, so _seq_key's
provenance part is None and nothing here needs the tag fallback path).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the project root is on sys.path so `scripts` is importable (pytest
# only adds tests/ to sys.path; mirror test_timing_chain.py / test_wave_detect.py).
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from scripts import postshock_fade as pf

NS = pf.NS_PER_S  # 1e9
# Base timestamp: a large-ish epoch-ns so +45s offsets fit comfortably.
BASE = 1_788_000_000_000_000_000


def _row(ts, p, bid=None, ask=None, sid="S", mn=1, team="T", slug="slug",
         opp="Opp", book=None):
    """A mapentry-shaped sequence row (book optional side-channel)."""
    return {
        "ts_ns": ts, "p_map": p, "bid": bid, "ask": ask,
        "series_id": sid, "map_number": mn, "team": team,
        "market_slug": slug, "opponent": opp, "book": book,
    }


def _shock(direction, ts=BASE, p=0.70, prev=0.60, sid="S", mn=1, team="T",
           slug: str | None = "slug", n_book_obs=1, ended_by_restart=False):
    """A detect()-shaped shock record."""
    return {
        "direction": direction, "series_id": sid, "map_number": mn,
        "team": team, "shock_ts_ns": ts, "p_map": p, "prev_p_map": prev,
        "delta": p - prev, "n_book_obs": n_book_obs,
        "ended_by_restart": ended_by_restart, "market_slug": slug,
    }


# --------------------------------------------------------------------------
# §1.2/§1.3 population gates


def test_fallen_favorite_min_excludes_sub_040_on_both_arms():
    """prev_p_map < 0.40 is excluded on DOWN AND on UP (mirror)."""
    rows = [_row(BASE, 0.70, 0.85, 0.88), _row(BASE + 5 * NS, 0.70, 0.80, 0.82)]
    shocks = [_shock("down", prev=0.39), _shock("up", prev=0.39)]
    entries, funnel, _ = pf.simulate_entries(shocks, pf.sequences_by_key(rows))
    assert funnel["down"]["shocks"] == 1
    assert funnel["down"]["priced"] == 1
    assert funnel["down"]["prev>=0.40"] == 0
    assert funnel["up"]["shocks"] == 1
    assert funnel["up"]["priced"] == 1
    assert funnel["up"]["prev>=0.40"] == 0
    assert entries == []


def test_restart_and_missing_slug_exclude():
    """ended_by_restart excludes; missing market_slug excludes."""
    rows = [
        _row(BASE, 0.70, 0.85, 0.88, team="TR"),
        _row(BASE + 5 * NS, 0.70, 0.80, 0.82, team="TR"),
        _row(BASE + 100 * NS, 0.70, 0.85, 0.88, team="TS"),
        _row(BASE + 105 * NS, 0.70, 0.80, 0.82, team="TS"),
    ]
    shocks = [
        _shock("down", ts=BASE, team="TR", ended_by_restart=True),
        _shock("down", ts=BASE + 100 * NS, team="TS", slug=None),
    ]
    entries, funnel, _ = pf.simulate_entries(shocks, pf.sequences_by_key(rows))
    assert funnel["down"]["shocks"] == 2
    assert funnel["down"]["priced"] == 2
    assert funnel["down"]["prev>=0.40"] == 2
    assert funnel["down"]["no-restart"] == 1   # restart shock dropped here
    assert funnel["down"]["slug"] == 0         # slug-less shock dropped here
    assert funnel["down"]["alive"] == 0
    assert entries == []


def test_dead_shock_row_not_rescued_by_live_later_row():
    """A dead shock row (bid/ask None) is NOT rescued by a live later row."""
    rows = [
        _row(BASE, 0.70, None, None),          # shock row: dead book
        _row(BASE + 5 * NS, 0.70, 0.80, 0.82),  # live, gap >= gated
    ]
    shocks = [_shock("down")]
    entries, funnel, _ = pf.simulate_entries(shocks, pf.sequences_by_key(rows))
    assert funnel["down"]["shocks"] == 1
    assert funnel["down"]["slug"] == 1
    assert funnel["down"]["alive"] == 0
    assert entries == []


def test_staleness_gate_boundary():
    """gap 0.099 excluded; 0.10 included (>= STALENESS_GATE)."""
    # p=0.0 grounds the arm_gap to bid (exactly binary-friendly near the gate).
    rows = [
        _row(BASE, 0.0, 0.099, 0.11, team="T_lo"),
        _row(BASE + 4 * NS, 0.0, 0.07, 0.09, team="T_lo"),
        _row(BASE, 0.0, 0.10, 0.12, team="T_hi"),
        _row(BASE + 4 * NS, 0.0, 0.07, 0.09, team="T_hi"),
    ]
    shocks = [
        _shock("down", ts=BASE, team="T_lo"),   # gap 0.099 -> gate drops
        _shock("down", ts=BASE, team="T_hi"),   # gap 0.10  -> passes
    ]
    entries, funnel, _ = pf.simulate_entries(shocks, pf.sequences_by_key(rows))
    assert funnel["down"]["alive"] == 2
    assert funnel["down"]["gate"] == 1          # only the 0.10 shock gated
    assert funnel["down"]["persist-raw"] == 1
    assert [e["team"] for e in entries] == ["T_hi"]
    assert entries[0]["staleness_gap"] == pytest.approx(0.10)


def test_persistence_uses_next_live_quote_in_window():
    """NEXT live quote strictly after shock, within 45s, decides; skips non-live."""
    # (a) a non-live row in between is skipped -> the +2s live gap 0.20 counts.
    seq_a = [
        _row(BASE, 0.0, None, None),
        _row(BASE + NS, 0.0, None, None),       # dead row inside window: skipped
        _row(BASE + 2 * NS, 0.0, 0.20, 0.22),
    ]
    assert pf._persistence_ok("down", seq_a, BASE, None) is True

    # (b) the FIRST live quote decides: 0.049 fails even though a later quote
    #     inside 45s shows 0.20.
    seq_b = [
        _row(BASE + NS, 0.0, 0.049, 0.06),
        _row(BASE + 2 * NS, 0.0, 0.20, 0.22),
    ]
    assert pf._persistence_ok("down", seq_b, BASE, None) is False

    # (c) a live quote at 46s is outside the 45s window -> no entry.
    seq_c = [_row(BASE + 46 * NS, 0.0, 0.20, 0.22)]
    assert pf._persistence_ok("down", seq_c, BASE, None) is False


def test_dedup_per_arm_earliest_wins():
    """Dedup happens per arm: earliest persisted shock wins; persist-failed
    earlier shock consumes nothing; DOWN + UP on the same key both survive."""
    ts_c, ts_a, ts_b, ts_u = (BASE, BASE + 10 * NS, BASE + 20 * NS,
                              BASE + 30 * NS)
    rows = [
        _row(ts_c, 0.70, 0.85, 0.88),                    # C: shock row (earliest)
        _row(ts_c + 2 * NS, 0.70, 0.73, 0.76),           # C: persist-fail (gap .03)
        _row(ts_a, 0.70, 0.85, 0.88),                    # A: shock row
        _row(ts_a + 4 * NS, 0.70, 0.80, 0.82),           # A: persist ok (gap .10)
        _row(ts_b, 0.70, 0.85, 0.88),                    # B: shock row (later)
        _row(ts_b + 4 * NS, 0.70, 0.80, 0.82),           # B: persist ok
        _row(ts_u, 0.70, 0.50, 0.55),                    # U: UP shock row
        _row(ts_u + 4 * NS, 0.70, 0.58, 0.62),           # U: UP persist ok
    ]
    shocks = [
        _shock("down", ts=ts_c, team="T", prev=0.60),    # persist FAILS
        _shock("down", ts=ts_a, team="T", prev=0.60),    # wins the slot
        _shock("down", ts=ts_b, team="T", prev=0.60),
        _shock("up", ts=ts_u, team="T", prev=0.60),      # survives its own arm
    ]
    entries, funnel, _ = pf.simulate_entries(shocks, pf.sequences_by_key(rows))
    # Persistence failed for C (consumes nothing); A and B persisted -> dedup
    # keeps A (earliest).  DOWN and UP arms dedup independently.
    assert funnel["down"]["alive"] == 3
    assert funnel["down"]["gate"] == 3
    assert funnel["down"]["persist-raw"] == 2
    assert funnel["down"]["deduped"] == 1
    assert funnel["down"]["dedup_slots_lost"] == 1
    assert funnel["up"]["deduped"] == 1
    arms = {e["arm"] for e in entries}
    assert arms == {"down", "up"}
    down = [e for e in entries if e["arm"] == "down"]
    assert [e["shock_ts_ns"] for e in down] == [ts_a]   # earliest persisted wins


def test_terminal_cutoff():
    """A shock at/after terminal is dropped (after_terminal); persistence
    ignores quotes at/after terminal."""
    ts0 = BASE
    rows = [
        _row(ts0, 0.70, 0.85, 0.88),
        _row(ts0 + 5 * NS, 0.70, 0.80, 0.82),
    ]
    shocks = [_shock("down", ts=ts0, prev=0.60)]
    terminal = ts0  # shock is AT terminal

    # Persistence itself must not read settlement-informed quotes.
    assert pf._persistence_ok(
        "down", rows, ts0, terminal=ts0 + 5 * NS) is False

    entries, funnel, _ = pf.simulate_entries(
        shocks, pf.sequences_by_key(rows), terminal_by_map={("S", 1): terminal})
    assert funnel["down"]["gate"] == 1
    assert funnel["down"]["persist-raw"] == 0
    assert funnel["down"]["after_terminal"] == 1
    assert entries == []

    # And a strictly-before-terminal quote still decides.
    assert pf._persistence_ok(
        "down", [_row(ts0 + 5 * NS, 0.70, 0.80, 0.82)],
        ts0, terminal=ts0 + 30 * NS) is True


def test_funnel_exact_per_stage_one_arm():
    """Exact per-stage funnel counts for a small synthetic DOWN pool."""
    rows: list[dict] = []
    shocks: list[dict] = []
    # Each stage drops exactly one shock: 8 in -> 1 entry.
    stages = [
        # (team, prev, n_book_obs, ended_by_restart, slug, shock_bid, shock_ask)
        ("T1", 0.30, 1, False, "slug", 0.85, 0.88),   # prev gate
        ("T2", 0.60, 0, False, "slug", 0.85, 0.88),   # priced gate
        ("T3", 0.60, 1, True, "slug", 0.85, 0.88),    # no-restart gate
        ("T4", 0.60, 1, False, None, 0.85, 0.88),     # slug gate
        ("T5", 0.60, 1, False, "slug", None, None),   # alive (dead book) gate
        ("T6", 0.60, 1, False, "slug", 0.75, 0.78),   # staleness gate (gap .05)
        ("T7", 0.60, 1, False, "slug", 0.85, 0.88),   # persistence gate
        ("T8", 0.60, 1, False, "slug", 0.85, 0.88),   # survives
    ]
    for i, (team, prev, nbo, restart, slug, bid, ask) in enumerate(stages):
        ts = BASE + i * 200 * NS
        rows.append(_row(ts, 0.70, bid, ask, team=team, slug=slug))
        # Persistence rows (skipped when the shock row is dead / gate-dropped).
        if team == "T7":        # fails persistence (gap 0.03)
            rows.append(_row(ts + 4 * NS, 0.70, 0.73, 0.76, team=team, slug=slug))
        elif team == "T8":      # survives (gap 0.10)
            rows.append(_row(ts + 4 * NS, 0.70, 0.80, 0.82, team=team, slug=slug))
        shocks.append(_shock("down", ts=ts, team=team, prev=prev,
                             n_book_obs=nbo, ended_by_restart=restart,
                             slug=slug))
    entries, funnel, _ = pf.simulate_entries(shocks, pf.sequences_by_key(rows))
    assert funnel["down"] == {
        "shocks": 8, "priced": 7, "prev>=0.40": 6, "no-restart": 5, "slug": 4,
        "alive": 3, "gate": 2, "persist-raw": 1, "deduped": 1, "series": 1,
        "shock_row_not_found": 0, "after_terminal": 0, "dedup_slots_lost": 0,
    }
    assert [e["team"] for e in entries] == ["T8"]
    assert "up" in funnel


def test_converge_s():
    """_converge_s: first later live quote with gap < 0.05 before terminal."""
    ts0 = BASE
    assert pf._converge_s("down", [
        _row(ts0 + 10 * NS, 0.0, 0.03, 0.05),
    ], ts0, None) == 10.0

    # First qualifying later quote is the +20s one (the +10s gap 0.20 is >= gate).
    assert pf._converge_s("down", [
        _row(ts0 + 10 * NS, 0.0, 0.20, 0.22),
        _row(ts0 + 20 * NS, 0.0, 0.03, 0.05),
    ], ts0, None) == 20.0

    # Never converges before terminal.
    assert pf._converge_s("down", [
        _row(ts0 + 10 * NS, 0.0, 0.20, 0.22),
    ], ts0, None) is None

    # A converging quote at/after the terminal cutoff is invisible.
    assert pf._converge_s("down", [
        _row(ts0 + 10 * NS, 0.0, 0.03, 0.05),
    ], ts0, terminal=ts0 + 5 * NS) is None


def test_converge_within_15s_flag():
    """Entry-level converge_within_15s is True only when converge_s <= 15s."""
    ts0 = BASE
    rows = [
        _row(ts0, 0.70, 0.85, 0.88, team="C1"),
        _row(ts0 + 4 * NS, 0.70, 0.80, 0.82, team="C1"),   # persist ok
        _row(ts0 + 10 * NS, 0.70, 0.73, 0.76, team="C1"),  # converges at 10s
        _row(ts0, 0.70, 0.85, 0.88, team="C2", slug="slug2"),
        _row(ts0 + 4 * NS, 0.70, 0.80, 0.82, team="C2", slug="slug2"),
        _row(ts0 + 20 * NS, 0.70, 0.73, 0.76, team="C2", slug="slug2"),
    ]
    shocks = [
        _shock("down", ts=ts0, team="C1", prev=0.60),
        _shock("down", ts=ts0, team="C2", prev=0.60, slug="slug2"),
    ]
    entries, _, _ = pf.simulate_entries(shocks, pf.sequences_by_key(rows))
    by_team = {e["team"]: e for e in entries}
    assert by_team["C1"]["converge_s"] == 10.0
    assert by_team["C1"]["converge_within_15s"] is True
    assert by_team["C2"]["converge_s"] == 20.0
    assert by_team["C2"]["converge_within_15s"] is False


def test_diagnostics_splits_and_global_dedup():
    """diagnostics(): converged/never split, cross-arm overlap, global dedup."""
    entries = [
        # down, key ('S','1','slug'), converge at 5s, WINS
        {"arm": "down", "series_id": "S", "map_number": 1,
         "market_slug": "slug", "shock_ts_ns": 100, "top_size": 2.0,
         "top_size_usd": 0.6, "converge_s": 5.0, "converge_within_15s": True,
         "persist_any_ok": True, "sr_ts_delta_ns": 0, "won": True,
         "entry_price": 0.30},
        # down, key ('S','2','slugB'), never converges, LOSES
        {"arm": "down", "series_id": "S", "map_number": 2,
         "market_slug": "slugB", "shock_ts_ns": 200, "top_size": None,
         "top_size_usd": None, "converge_s": None,
         "converge_within_15s": False, "persist_any_ok": False,
         "sr_ts_delta_ns": 2, "won": False, "entry_price": 0.25},
        # down, key ('S','1','slug') DUPLICATE of #1 -> global-dedup drops it
        {"arm": "down", "series_id": "S", "map_number": 1,
         "market_slug": "slug", "shock_ts_ns": 300, "top_size": None,
         "top_size_usd": None, "converge_s": None,
         "converge_within_15s": False, "persist_any_ok": True,
         "sr_ts_delta_ns": 0, "won": True, "entry_price": 0.20},
        # up, key ('S','3','slugC'), converges, unresolved
        {"arm": "up", "series_id": "S", "map_number": 3,
         "market_slug": "slugC", "shock_ts_ns": 400, "top_size": 5.0,
         "top_size_usd": 3.0, "converge_s": 2.0, "converge_within_15s": True,
         "persist_any_ok": True, "sr_ts_delta_ns": 0, "won": None,
         "entry_price": 0.60},
        # up, OVERLAP key ('S','1','slug') shared with a down entry
        {"arm": "up", "series_id": "S", "map_number": 1,
         "market_slug": "slug", "shock_ts_ns": 500, "top_size": None,
         "top_size_usd": None, "converge_s": None,
         "converge_within_15s": False, "persist_any_ok": True,
         "sr_ts_delta_ns": 0, "won": True, "entry_price": 0.55},
    ]
    d = pf.diagnostics(entries)

    # --- converged vs never-converged split (down arm) ---
    dd = d["per_arm"]["down"]
    assert dd["converged"]["n"] == 1          # entry #1 (resolved, won)
    assert dd["converged"]["n_won"] == 1
    assert dd["converged"]["win_rate"] == 1.0
    assert dd["converged"]["mean_settled_edge"] == pytest.approx(1.0 - 0.30)
    assert dd["never_converged"]["n"] == 2    # entries #2, #3
    assert dd["never_converged"]["n_won"] == 1
    assert dd["never_converged"]["win_rate"] == 0.5
    assert dd["never_converged"]["mean_settled_edge"] == pytest.approx(
        ((0.0 - 0.25) + (1.0 - 0.20)) / 2.0)
    assert dd["n_never_converge"] == 2
    assert dd["n_converge_within_15s"] == 1
    assert dd["n_persist_flips"] == 1

    # --- cross-arm overlap ---
    assert d["cross_arm_overlap_keys"] == 1  # ('S','1','slug') present both arms

    # --- global (cross-arm) dedup counts ---
    gd = d["global_dedup"]
    assert gd["n_entries"] == 3               # #1, #2, #4 (dup #3, #5 dropped)
    assert gd["n_down_entries"] == 2          # #1, #2
    assert gd["n_up_entries"] == 1            # #4
    assert gd["down"]["n"] == 2
    assert gd["down"]["n_won"] == 1
    assert gd["down"]["win_rate"] == 0.5
    assert gd["down"]["mean_settled_edge"] == pytest.approx(
        ((1.0 - 0.30) + (0.0 - 0.25)) / 2.0)

    # sr_ts_delta_ns nonzero: entry #2 only.
    assert d["sr_ts_delta_ns_nonzero"] == 1


def test_top_size_by_arm_and_missing_book():
    """DOWN uses book.bid_size; UP uses book.ask_size; None without a book."""
    rows = [
        _row(BASE, 0.70, 0.85, 0.88, team="D1", book={"bid_size": 3.0,
                                                      "ask_size": 7.0}),
        _row(BASE + 4 * NS, 0.70, 0.80, 0.82, team="D1"),
        _row(BASE, 0.70, 0.50, 0.55, team="U1", book={"bid_size": 3.0,
                                                      "ask_size": 7.0}),
        _row(BASE + 4 * NS, 0.70, 0.58, 0.62, team="U1"),
        _row(BASE, 0.70, 0.85, 0.88, team="D2", mn=2),   # no book side-channel
        _row(BASE + 4 * NS, 0.70, 0.80, 0.82, team="D2", mn=2),
    ]
    shocks = [
        _shock("down", ts=BASE, team="D1", prev=0.60),
        _shock("up", ts=BASE, team="U1", prev=0.60),
        _shock("down", ts=BASE, team="D2", mn=2, prev=0.60),
    ]
    entries, _, _ = pf.simulate_entries(shocks, pf.sequences_by_key(rows))
    by_team = {e["team"]: e for e in entries}
    assert by_team["D1"]["top_size"] == 3.0          # DOWN -> bid_size
    assert by_team["D1"]["top_size_usd"] == pytest.approx(3.0 * 0.15)
    assert by_team["U1"]["top_size"] == 7.0          # UP -> ask_size
    assert by_team["D2"]["top_size"] is None         # no book -> None
    assert by_team["D2"]["top_size_usd"] is None