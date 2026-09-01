"""Tests for scripts.crossin_backtest (H-CROSSIN1, describe-only).

Analysis only (slow loop, iron rail 1): locks in the executable-price
cross-in semantics per the 2026-08-31 dispatch — the engine-faithful
population (slate rows excluded, band-skipped evaluations, alive own-row
book, phase-1/2 dedup key), the executable orientation (long = ask,
short = 1 - bid, matching src/gridbot/instruments/mapentry.py side_ask),
the first-cross + next-quote persistence entry rule, the conservative
first-cross entry price, the one-entry-per-(series, map, slug) dedup, the
map-winner settlement via a MOCKED oracle subprocess, and the BOOKPIN1
design/holdout split on the ns cutoff.

No network, no keyring, no repo data — every input is a synthetic JSONL in
tmp_path; the frozen oracle is never invoked (subprocess.run patched).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from scripts import crossin_backtest as cb

NS = 1_000_000_000
SLUG = "astatc-cs2-va-5s-2026-08-27-map1"


def mk_row(ts_ns, *, series_id="s1", map_number=1, round_number=2,
           team="VA", opponent="5s", p_map=0.85, market_slug=SLUG,
           us_side="long", bid=0.40, ask=0.44, hypothesis_id="H-MAPENTRY1",
           src_hypothesis_id="H-MODELENTRY1", **kw) -> dict:
    row = {
        "type": "mapentry", "hypothesis_id": hypothesis_id,
        "src_hypothesis_id": src_hypothesis_id, "series_id": series_id,
        "map_number": map_number, "round_number": round_number,
        "team": team, "opponent": opponent, "p_map": p_map,
        "market_slug": market_slug, "us_side": us_side,
        "bid": bid, "ask": ask, "ts_ns": int(ts_ns),
    }
    row.update(kw)
    return row


def write_jsonl(tmp_path: Path, rows: list[dict]) -> str:
    p = tmp_path / "mapentry-20260101.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(p)


def pattern_of(tmp_path: Path) -> str:
    return str(tmp_path / "mapentry-*.jsonl")


def fake_winners(monkeypatch, winners: dict, excluded=None):
    """Patch subprocess.run so the frozen-oracle call returns canned winners."""

    def fake_run(cmd, *a, **k):
        res = subprocess.CompletedProcess(cmd, 0)
        res.stdout = json.dumps({"winners": winners, "excluded": excluded or []})
        res.stderr = ""
        return res

    monkeypatch.setattr(cb.subprocess, "run", fake_run)


# ── orientation ───────────────────────────────────────────────────────────────

def test_exec_price_orientation():
    # long pays the ask of the named-team book
    assert cb.exec_price("long", 0.40, 0.44) == 0.44
    # short buys the No leg: 1 - bid
    assert cb.exec_price("short", 0.40, 0.44) == 0.60
    assert cb.exec_price("short", 0.97, 0.99) == 0.03
    assert cb.exec_price("other", 0.40, 0.44) is None


def test_dead_book_rejected():
    assert cb.book_top({"bid": 0, "ask": 0.5}) is None
    assert cb.book_top({"bid": None, "ask": 0.5}) is None
    assert cb.book_top({"bid": 0.6, "ask": 0.4}) is None  # crossed
    assert cb.book_top({"bid": 0.40, "ask": 0.44}) == (0.40, 0.44)


# ── population ────────────────────────────────────────────────────────────────

def test_population_excludes_slate_and_in_band(tmp_path):
    rows = [
        mk_row(1 * NS, p_map=0.85),                    # valid skip (t0)
        mk_row(2 * NS, src_hypothesis_id="H-MAPENTRY1-SLATE",
               p_map=0.85),                            # slate — excluded
        mk_row(3 * NS, p_map=0.60),                    # in band — excluded
    ]
    write_jsonl(tmp_path, rows)
    skips, _tl, c = cb.load_population(pattern_of(tmp_path))
    assert len(skips) == 1
    assert c["slate_rows_excluded"] == 1
    assert c["p_map_missing_or_in_band"] == 1


def test_population_dedup_first_evaluation_wins(tmp_path):
    # same (series, map, round, slug, side) key, two evaluations; first wins
    rows = [
        mk_row(10 * NS, round_number=2),
        mk_row(5 * NS, round_number=2),   # earlier ts for same key -> t0
    ]
    write_jsonl(tmp_path, rows)
    skips, _tl, _c = cb.load_population(pattern_of(tmp_path))
    assert len(skips) == 1 and skips[0]["t0"] == 5 * NS


def test_population_dead_book_at_eval_skipped(tmp_path):
    rows = [mk_row(1 * NS, bid=0, ask=0)]
    write_jsonl(tmp_path, rows)
    skips, _tl, c = cb.load_population(pattern_of(tmp_path))
    assert skips == [] and c["dead_book_at_eval"] == 1


def test_population_torn_line_and_day_join(tmp_path):
    a = tmp_path / "mapentry-20260101.jsonl"
    b = tmp_path / "mapentry-20260102.jsonl"
    a.write_text(json.dumps(mk_row(1 * NS)) + "\n{torn json\n")
    b.write_text(json.dumps(mk_row(2 * NS, round_number=3, p_map=0.30)) + "\n")
    skips, _tl, _c = cb.load_population(str(tmp_path / "mapentry-*.jsonl"))
    assert len(skips) == 2  # both days loaded, torn line skipped


def test_population_missing_slug_or_side_skipped(tmp_path):
    rows = [
        mk_row(1 * NS, market_slug=None),
        mk_row(2 * NS, us_side=None),
    ]
    write_jsonl(tmp_path, rows)
    skips, _tl, c = cb.load_population(pattern_of(tmp_path))
    assert skips == [] and c["no_slug_or_side"] == 2


# ── entry rule ────────────────────────────────────────────────────────────────

def _scenario(tmp_path, quote_rows, t0_row=None, t0_s=1):
    """t0 skip row plus a quote stream [(ts_s, bid, ask)]; returns entries.

    Quote rows carry an IN-BAND p_map so they are quote-bearing on the
    timeline but never skip candidates themselves.
    """
    rows = [t0_row or mk_row(t0_s * NS)]
    for ts_s, bid, ask in quote_rows:
        rows.append(mk_row(ts_s * NS, p_map=0.60, bid=bid, ask=ask))
    write_jsonl(tmp_path, rows)
    skips, tl, _ = cb.load_population(pattern_of(tmp_path))
    assert len(skips) == 1  # only the t0 row is a skip event
    entries, ec = cb.simulate_entries(skips, tl)
    return entries, ec


def test_cross_with_persistence_enters_at_first_ask(tmp_path):
    # t0=1s, p_map=0.85 (above band); quotes: 10s out -> 100s cross -> 160s confirm
    entries, ec = _scenario(
        tmp_path,
        [(10, 0.95, 0.96), (100, 0.68, 0.70), (160, 0.70, 0.72)])
    assert ec["failed_persistence"] == 0
    assert len(entries) == 1
    assert entries[0]["entry_price"] == 0.70   # first crossing ask, not confirm
    assert entries[0]["arm"] == "above"


def test_terminal_cutoff_excludes_settlement_informed_quotes(tmp_path):
    # F1 (registry draft pin): the map's oracle-terminal observation at 90s
    # makes the 100s crossing settlement-informed — no entry may form on it.
    rows = [mk_row(1 * NS)]
    for ts_s, bid, ask in [(10, 0.95, 0.96), (100, 0.68, 0.70), (160, 0.70, 0.72)]:
        rows.append(mk_row(ts_s * NS, p_map=0.60, bid=bid, ask=ask))
    write_jsonl(tmp_path, rows)
    skips, tl, _ = cb.load_population(pattern_of(tmp_path))
    key = (skips[0]["series_id"], skips[0]["map_number"])
    entries, ec = cb.simulate_entries(skips, tl, {key: 90 * NS})
    assert entries == [] and ec["no_cross_in_300s"] == 1
    # terminal AFTER the whole stream: identical to no cutoff
    entries2, _ = cb.simulate_entries(skips, tl, {key: 500 * NS})
    assert len(entries2) == 1 and entries2[0]["entry_price"] == 0.70
    # terminal between cross and confirm: crossing stands, confirm excluded
    entries3, ec3 = cb.simulate_entries(skips, tl, {key: 150 * NS})
    assert entries3 == [] and ec3["failed_persistence"] == 1


def test_cross_without_persistence_no_entry(tmp_path):
    # crossing at 100s then next quote leaves the band -> failed persistence
    entries, ec = _scenario(
        tmp_path,
        [(100, 0.68, 0.70), (160, 0.90, 0.91)])
    assert entries == [] and ec["failed_persistence"] == 1


def test_no_next_quote_in_window_is_failed_persistence(tmp_path):
    # crossing but no later quote at all
    entries, ec = _scenario(tmp_path, [(100, 0.68, 0.70)])
    assert entries == [] and ec["failed_persistence"] == 1


def test_cross_outside_300s_window_ignored(tmp_path):
    entries, ec = _scenario(tmp_path, [(400, 0.68, 0.70), (450, 0.68, 0.70)])
    assert entries == [] and ec["no_cross_in_300s"] == 1


def test_confirm_outside_180s_window_no_entry(tmp_path):
    # confirm arrives 190s after the crossing row -> out of persistence window
    entries, ec = _scenario(
        tmp_path,
        [(100, 0.68, 0.70), (100 + 190, 0.70, 0.72)])
    assert entries == [] and ec["failed_persistence"] == 1


def test_below_band_control_arm(tmp_path):
    t0_row = mk_row(1 * NS, p_map=0.30, us_side="short", bid=0.90, ask=0.92)
    # short executable = 1 - 0.90 = 0.10 at t0; crossing when bid <= 0.50
    entries, _ = _scenario(
        tmp_path,
        [(100, 0.40, 0.42), (160, 0.42, 0.44)], t0_row=t0_row)
    assert len(entries) == 1
    assert entries[0]["arm"] == "below"
    assert entries[0]["entry_price"] == 0.60  # 1 - bid at the crossing row


# ── dedup ─────────────────────────────────────────────────────────────────────

def test_one_entry_per_map_book(tmp_path):
    rows = [
        # book 1: two skip events (different rounds), both cross -> 1 entry
        mk_row(1 * NS, round_number=2),
        mk_row(1000 * NS, round_number=3, p_map=0.85),
        mk_row(100 * NS, p_map=0.60, bid=0.68, ask=0.70),
        mk_row(160 * NS, p_map=0.60, bid=0.70, ask=0.72),
        mk_row(1100 * NS, p_map=0.60, bid=0.68, ask=0.70),
        mk_row(1160 * NS, p_map=0.60, bid=0.70, ask=0.72),
        # book 2 (different slug, same series/map) — its own slot
        mk_row(2000 * NS, round_number=4, market_slug=SLUG + "b"),
        mk_row(2100 * NS, market_slug=SLUG + "b", p_map=0.60,
               bid=0.68, ask=0.70),
        mk_row(2160 * NS, market_slug=SLUG + "b", p_map=0.60,
               bid=0.70, ask=0.72),
    ]
    write_jsonl(tmp_path, rows)
    skips, tl, _ = cb.load_population(pattern_of(tmp_path))
    entries, ec = cb.simulate_entries(skips, tl)
    assert ec["dedup_slots_lost"] == 1
    assert len(entries) == 2
    assert {e["slug"] for e in entries} == {SLUG, SLUG + "b"}


# ── settlement (mocked oracle) ────────────────────────────────────────────────

def test_resolution_short_orientation_wins_via_team_win(tmp_path):
    # short on opponent-named book (named team = 5s, our team = VA):
    # entry at 1 - bid; VA wins the map => won = True
    t0_row = mk_row(1 * NS, p_map=0.30, us_side="short", bid=0.90, ask=0.92,
                    team="VA", opponent="5s")
    entries, _ = _scenario(
        tmp_path,
        [(100, 0.40, 0.42), (160, 0.42, 0.44)], t0_row=t0_row)
    won, status = cb.resolve(entries[0], {"s1:1": {"winner": "VA", "loser": "5s"}}, [])
    assert won is True and status == "resolved"


def test_resolution_unresolved_never_guessed():
    row = {"series_id": "s1", "map_number": 1, "team": "GHOST"}
    won, status = cb.resolve(row, {"s1:1": {"winner": "VA", "loser": "5s"}}, [])
    assert won is None and status == "team_unmatched_to_winner_or_loser"
    won, status = cb.resolve(row, {}, [{"series_id": "s1", "map_number": 1,
                                        "reason": "ambiguous_winner"}])
    assert won is None and status == "excluded:ambiguous_winner"
    won, status = cb.resolve(row, {}, [])
    assert won is None and status == "no_map_winner_in_capture"


# ── holdout split ─────────────────────────────────────────────────────────────

def test_holdout_split_on_ns_cutoff(tmp_path):
    rows = [
        mk_row(1 * NS, series_id="pre"),    # mapped before cutoff
        mk_row(1 * NS, series_id="post"),   # mapped at/after cutoff
        mk_row(1 * NS, series_id="unmapped"),  # never mapped -> design
    ]
    write_jsonl(tmp_path, rows)
    skips, _tl, _ = cb.load_population(pattern_of(tmp_path))
    first = {"pre": cb.HOLDOUT_CUTOFF_NS - 1, "post": cb.HOLDOUT_CUTOFF_NS}
    design, holdout = cb.split_holdout(skips, first)
    assert [r["series_id"] for r in design] == ["pre", "unmapped"]
    assert [r["series_id"] for r in holdout] == ["post"]


# ── pipeline end-to-end with mocked oracle ────────────────────────────────────

def test_pipeline_end_to_end_with_mocked_oracle(tmp_path, monkeypatch):
    rows = [
        # above-band candidate: crosses into band and persists
        mk_row(1 * NS, series_id="s1", p_map=0.85),
        mk_row(100 * NS, p_map=0.60, bid=0.68, ask=0.70),
        mk_row(160 * NS, p_map=0.60, bid=0.70, ask=0.72),
        # below-band control: short, crosses into band and persists
        mk_row(1000 * NS, series_id="s2", p_map=0.30, us_side="short",
               bid=0.90, ask=0.92),
        mk_row(1100 * NS, p_map=0.60, bid=0.40, ask=0.42),
        mk_row(1160 * NS, p_map=0.60, bid=0.42, ask=0.44),
        # slate copy of the above skip — must not add a second entry
        mk_row(1200 * NS, series_id="s1", p_map=0.85,
               src_hypothesis_id="H-MAPENTRY1-SLATE"),
        mk_row(1300 * NS, p_map=0.60, bid=0.68, ask=0.70),
        mk_row(1360 * NS, p_map=0.60, bid=0.70, ask=0.72),
    ]
    write_jsonl(tmp_path, rows)
    (tmp_path / "series_state-20260101.jsonl").write_text("")  # oracle input
    fake_winners(monkeypatch, {
        "s1:1": {"winner": "VA", "loser": "5s"},
        "s2:1": {"winner": "5s", "loser": "VA"},  # s2's team VA loses the map
    })
    skips, timeline, _c = cb.load_population(pattern_of(tmp_path))
    design, _holdout = cb.split_holdout(
        skips, cb.series_first_mapped_ns(str(tmp_path / "none-*.jsonl")))
    entries, _ec = cb.simulate_entries(design, timeline)
    winners, excluded = cb.load_map_winners(str(tmp_path / "series_state-*.jsonl"))
    for e in entries:
        e["won"], e["res_status"] = cb.resolve(e, winners, excluded)
    arms = {a: cb.summarize([e for e in entries if e["arm"] == a])
            for a in ("above", "below")}
    assert len(entries) == 2
    assert arms["above"]["n_won"] == 1
    assert arms["below"]["n_won"] == 0  # control expected <= 0 edge
    # above arm: entry 0.70, won -> edge +0.30
    assert arms["above"]["mean_settled_edge"] == pytest.approx(0.30)
    # below arm: entry 0.60, lost -> edge -0.60
    assert arms["below"]["mean_settled_edge"] == pytest.approx(-0.60)
