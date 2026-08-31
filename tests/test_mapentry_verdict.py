"""H-MAPENTRY1 verdict wiring — the three frozen clauses + starvation.

Every test here encodes a trap from
docs/superpowers/specs/2026-08-30-mapentry-verdict-wiring.md. They are written
to FAIL against the pre-build tree (the module does not exist), and each one
names the production change that makes it pass.
"""
from __future__ import annotations

import json

import pytest

from gridbot.instruments import mapentry_verdict as mv


def _row(**over):
    """A real-shaped qualifying mapentry row (verbatim field set from capture)."""
    base = {
        "type": "mapentry",
        "hypothesis_id": "H-MAPENTRY1",
        "src_hypothesis_id": "H-MAPENTRY1",
        "series_id": "2997461",
        "map_number": 2,
        "round_number": 1,
        "side": "ct",
        "team": "Total Domination",
        "opponent": "Omega",
        "a": 0,
        "b": 0,
        "q_round": 0.4986,
        "p_map": 0.4998,
        "dedup_extra": "",
        "market_slug": "astatc-cs2-omg-td-2026-08-30-map2",
        "us_side": "short",
        "bid": 0.57,
        "ask": 0.60,
        "side_ask": 0.43,
        "entry_price_max": 0.4598,
        "qualifies": True,
        "skip_reason": None,
        "src_ts_ns": None,
        "grid_ts_ns": 1788074727253758998,
        "ts_ns": 1788074728271983142,
    }
    base.update(over)
    return base


# --- D1: right file, right type -------------------------------------------

def test_load_reads_mapentry_files_not_instances(tmp_path):
    """D1. verdict.load_instances globs instances-*.jsonl and filters
    type=="instance", which doubly excludes mapentry rows. The loader must
    glob mapentry-*.jsonl instead."""
    (tmp_path / "mapentry-20260830.jsonl").write_text(json.dumps(_row()) + "\n")
    (tmp_path / "instances-20260830.jsonl").write_text(
        json.dumps({"type": "instance", "hypothesis_id": "H-MODELENTRY1"}) + "\n"
    )
    rows = mv.load_mapentry_rows(str(tmp_path / "mapentry-*.jsonl"))
    assert len(rows) == 1
    assert rows[0]["hypothesis_id"] == "H-MAPENTRY1"


def test_load_rejects_rows_of_other_type(tmp_path):
    """D1. A mapentry file may carry other row types; only type=="mapentry"
    belongs to the recording population."""
    p = tmp_path / "mapentry-20260830.jsonl"
    p.write_text(
        json.dumps(_row())
        + "\n"
        + json.dumps({"type": "postquote", "series_id": "1"})
        + "\n"
        + "{ not json\n"
    )
    rows = mv.load_mapentry_rows(str(p))
    assert [r["type"] for r in rows] == ["mapentry"]


# --- provenance split: Queue-16 SLATE rows are a different population ------

def test_provenance_separates_slate_from_detector():
    """Queue-16 (a4d469f) added coverage rows that score far worse than
    detector rows; pooling them drags every calibration number."""
    assert mv.provenance(_row()) == "DETECTOR"
    assert mv.provenance(_row(src_hypothesis_id="H-MAPENTRY1-SLATE")) == "SLATE"
    assert mv.provenance(_row(dedup_extra="slate")) == "SLATE"


# --- D5: short legs invert -------------------------------------------------

def test_book_forecast_orients_the_short_leg():
    """D5. A short buys the No leg, so its book-implied probability is
    1 - mid, not mid. Pooling unoriented inverts the sign on every short."""
    long_row = _row(us_side="long", bid=0.40, ask=0.44)
    short_row = _row(us_side="short", bid=0.40, ask=0.44)
    assert mv.book_forecast(long_row) == pytest.approx(0.42)
    assert mv.book_forecast(short_row) == pytest.approx(0.58)


def test_book_forecast_side_ask_reading_uses_recorded_side_ask():
    """The registry says 'book price as forecast' without pinning mid vs the
    transacted price; both readings must be available and already oriented."""
    assert mv.book_forecast(_row(side_ask=0.43), reading="side_ask") == pytest.approx(0.43)


# --- D3: one-sided / missing books are NOT in clause (i)'s population ------

def test_book_forecast_none_when_book_is_not_two_sided():
    """D3. side_ask is frequently None on thin books. Clause (i) is defined
    over instances 'carrying a PMUS book at detection' — never impute."""
    assert mv.book_forecast(_row(bid=None, ask=None)) is None
    assert mv.book_forecast(_row(bid=0.0, ask=0.0)) is None
    assert mv.book_forecast(_row(bid=0.60, ask=0.55)) is None  # crossed
    assert mv.book_forecast(_row(side_ask=None), reading="side_ask") is None


# --- D4: the honest n is deduped per book ---------------------------------

def test_dedupe_keeps_one_entry_per_series_map_side():
    """D4. The qualifying population counts the same map book once per
    re-qualifying round and inflates any edge estimate. The engine's dedup key
    is (series, map, side)."""
    rows = [
        _row(round_number=1, ts_ns=100),
        _row(round_number=5, ts_ns=200),          # same book, later round
        _row(round_number=3, ts_ns=150, us_side="long"),   # other side
        _row(round_number=2, ts_ns=120, map_number=3),     # other map
    ]
    kept = mv.dedupe_per_book(rows)
    assert len(kept) == 3
    # first observation per book wins, matching the engine's one-entry rule
    first = [r for r in kept if r["map_number"] == 2 and r["us_side"] == "short"]
    assert len(first) == 1 and first[0]["ts_ns"] == 100


# --- D6: the oracle excludes, never guesses -------------------------------

def test_map_outcome_excludes_team_matching_neither_side():
    """D6. Rows whose team matches neither winner nor loser are excluded,
    never guessed by name similarity."""
    winners = {"2997461:2": {"winner": "Total Domination", "loser": "Omega"}}
    assert mv.map_outcome(_row(), winners) == 1.0
    assert mv.map_outcome(_row(team="Omega"), winners) == 0.0
    assert mv.map_outcome(_row(team="Total Dominaton"), winners) is None  # typo'd
    assert mv.map_outcome(_row(series_id="404"), winners) is None


# --- clause (ii) wording: the bar says QUINTILE bins -----------------------

def test_ece_uses_quintile_bins_per_the_frozen_bar():
    """The registry pins 'ECE(P_map) > 0.08 (quintile bins)' — five bins, not
    the ten pmap_calibration reports. A 10-bin number is not the bar."""
    pairs = [(0.10, 0.0)] * 10 + [(0.90, 1.0)] * 10
    assert mv.ece(pairs, bins=5) == pytest.approx(0.10, abs=1e-9)
    assert mv.ece.__defaults__ is not None
    # a perfectly calibrated set has zero ECE in any binning
    perfect = [(0.5, 1.0)] * 50 + [(0.5, 0.0)] * 50
    assert mv.ece(perfect, bins=5) == pytest.approx(0.0, abs=1e-9)


# --- the bar evaluation ----------------------------------------------------

def test_evaluate_bar_reports_all_three_clauses_and_starvation():
    """The registry text: FALSIFIED if ANY of (i) Brier(p_map) >= Brier(book),
    (ii) ECE > 0.08, (iii) at >=60 qualifying entries mean settled edge <= +2pt
    or the by-series CI includes 0. Plus the <5% starvation clause."""
    winners = {f"S{i}:1": {"winner": "W", "loser": "L"} for i in range(80)}
    rows = []
    for i in range(80):
        for rnd in range(3):
            rows.append(
                _row(
                    series_id=f"S{i}",
                    map_number=1,
                    round_number=rnd,
                    team="W" if i % 2 == 0 else "L",
                    us_side="long",
                    bid=0.50,
                    ask=0.54,
                    p_map=0.80,
                    qualifies=True,
                    ts_ns=1000 + rnd,
                )
            )
    res = mv.evaluate_bar(rows, winners)
    for key in ("clause_i", "clause_ii", "clause_iii", "starvation"):
        assert key in res, f"missing {key}"
    assert res["clause_i"]["brier_model"] is not None
    assert res["clause_i"]["brier_book"] is not None
    assert res["clause_ii"]["ece_quintile"] is not None
    assert res["clause_iii"]["n_deduped"] == 80  # D4 collapsed 240 rows -> 80 books
    assert res["starvation"]["qualifying_fraction"] == pytest.approx(1.0)
    assert res["verdict_reachable"] in (True, False)


def test_evaluate_bar_marks_underpowered_below_the_frozen_n():
    """The bar needs >=150 resolved instances and >=60 qualifying entries.
    Below that the answer is UNDER-POWERED, never PASS and never FALSIFIED."""
    winners = {"S0:1": {"winner": "W", "loser": "L"}}
    rows = [_row(series_id="S0", map_number=1, team="W", us_side="long")]
    res = mv.evaluate_bar(rows, winners)
    assert res["status"] == "UNDER_POWERED"


# --- reviewer finding 6: the main() early-return trap ----------------------

def test_mapentry_row_is_independent_of_the_instances_corpus():
    """glm-5.3 review finding 6: verdict.main() early-returns when
    load_instances() is empty. On a fresh box with populated mapentry files
    and no instances, the H-MAPENTRY1 row must still be produced."""
    from gridbot.instruments import verdict

    assert hasattr(verdict, "mapentry_summary_lines"), (
        "verdict must expose a mapentry path reachable without instances"
    )


def test_clause_ii_scores_the_same_population_as_clause_i():
    """The bar scopes itself to 'resolved instances carrying a PMUS book at
    detection', then narrows to 'qualifying entries' only in clause (iii).
    So clause (ii)'s ECE population is the with-book population, not the
    qualifying subset. The qualifying number is reported as reference."""
    winners = {f"S{i}:1": {"winner": "W", "loser": "L"} for i in range(80)}
    rows = []
    for i in range(80):
        # one qualifying row and two non-qualifying rows per series, all with books
        for j, qual in enumerate((True, False, False)):
            rows.append(
                _row(
                    series_id=f"S{i}",
                    map_number=1,
                    round_number=j,
                    team="W" if i % 2 == 0 else "L",
                    us_side="long",
                    bid=0.50,
                    ask=0.54,
                    p_map=0.80,
                    qualifies=qual,
                    ts_ns=1000 + j,
                )
            )
    res = mv.evaluate_bar(rows, winners)
    assert res["clause_i"]["n"] == 240, "clause (i) scores all resolved rows with a book"
    assert res["clause_ii"]["n"] == res["clause_i"]["n"], (
        "clause (ii) must score the bar's population, not the qualifying subset"
    )
    assert res["clause_ii"]["n_qualifying_reference"] == 80
    assert res["clause_ii"]["ece_qualifying_reference"] is not None


# --- H-MAPENTRY1-BOOKPIN1: book-mid pin + temporal holdout -----------------


def test_holdout_start_is_parsed_from_the_registry_row(tmp_path):
    """The holdout start is stored in H-MAPENTRY1-BOOKPIN1's own text; the
    reporting layer must read it from the registry, not hard-code a copy."""
    reg = tmp_path / "registry.jsonl"
    reg.write_text(
        json.dumps(
            {
                "hypothesis_id": "H-MAPENTRY1-BOOKPIN1",
                "hypothesis_text": (
                    "holdout = next 40 newly-mapped series recorded at or "
                    "after 2027-01-02T03:04:05Z; earlier series excluded"
                ),
            }
        )
        + "\n"
    )
    assert mv.holdout_start_iso(reg) == "2027-01-02T03:04:05Z"
    # missing registry -> the actual value the row carries as registered
    assert mv.holdout_start_iso(tmp_path / "absent.jsonl") == mv.HOLD_START_FALLBACK


def test_holdout_start_live_registry_matches_the_declared_value():
    """The tree's registry.jsonl currently carries the filled actual value."""
    assert mv.holdout_start_iso() == "2026-08-31T19:53:46Z"


def test_holdout_partial_below_threshold_and_excludes_pre_holdout_rows():
    """Holdout n counts resolved instances with a real two-sided book at or
    after the declared start; pre-start and bookless rows never count. Below
    150 the line is PARTIAL with holdout_declared=false."""
    start_ns = mv.iso_to_ns(mv.HOLD_START_FALLBACK)
    winners = {f"S{i}:1": {"winner": "W", "loser": "L"} for i in range(3)}
    rows = [
        _row(series_id=f"S{i}", map_number=1, team="W", us_side="long",
             ts_ns=start_ns + i + 1)
        for i in range(3)
    ]
    winners["OLD:1"] = {"winner": "W", "loser": "L"}
    rows.append(_row(series_id="OLD", map_number=1, team="W", us_side="long",
                     ts_ns=start_ns - 1))
    winners["NB:1"] = {"winner": "W", "loser": "L"}
    rows.append(
        _row(series_id="NB", map_number=1, team="W", us_side="long",
             ts_ns=start_ns + 99, bid=None, ask=None)  # no two-sided book
    )
    res = mv.evaluate_bar(rows, winners)
    assert res["holdout"]["start"] == mv.HOLD_START_FALLBACK
    assert res["holdout"]["n"] == 3
    assert res["holdout"]["bar"] == 150
    assert res["holdout_declared"] is False
    lines = mv.summary_lines(res)
    assert any(
        "holdout: PARTIAL" in l
        and "n=3 < 150" in l
        and "holdout_declared=false" in l
        for l in lines
    )


def test_holdout_ready_at_threshold():
    """>=150 held-out two-sided-book resolved instances flip holdout_declared
    and render READY — the only state in which a registry verdict is renderable."""
    start_ns = mv.iso_to_ns(mv.HOLD_START_FALLBACK)
    n = 150
    winners = {f"S{i}:1": {"winner": "W", "loser": "L"} for i in range(n)}
    rows = [
        _row(
            series_id=f"S{i}",
            map_number=1,
            team="W",
            us_side="long",
            bid=0.50,
            ask=0.54,
            p_map=0.80,
            qualifies=True,
            ts_ns=start_ns + 1000 + i,
        )
        for i in range(n)
    ]
    res = mv.evaluate_bar(rows, winners)
    assert res["holdout"]["n"] == 150
    assert res["holdout_declared"] is True
    lines = mv.summary_lines(res)
    assert any(
        "holdout: READY" in l
        and "n=150 >= 150" in l
        and "holdout_declared=true" in l
        for l in lines
    )


def test_holdout_start_override_is_respected():
    """evaluate_bar must honor an explicit holdout_start (dependency
    injection for tests and future re-declarations)."""
    winners = {"S0:1": {"winner": "W", "loser": "L"}}
    rows = [_row(series_id="S0", map_number=1, team="W", us_side="long", ts_ns=5000)]
    res = mv.evaluate_bar(rows, winners, holdout_start="1970-01-01T00:00:00Z")
    assert res["holdout"]["n"] == 1
    res = mv.evaluate_bar(rows, winners, holdout_start="2099-01-01T00:00:00Z")
    assert res["holdout"]["n"] == 0


def test_summary_lines_carry_bookpin_annotation_for_the_pinned_reading():
    """The H-MAPENTRY1 block must make the pinned comparator explicit:
    book MID is the bar (i) reading per H-MAPENTRY1-BOOKPIN1."""
    winners = {"S0:1": {"winner": "W", "loser": "L"}}
    rows = [_row(series_id="S0", team="W", us_side="long")]
    res = mv.evaluate_bar(rows, winners)  # default reading == pinned "mid"
    lines = mv.summary_lines(res)
    assert lines[0].startswith("  H-MAPENTRY1 [DETECTOR]")
    assert "[BOOKPIN: book=mid reading]" in lines[0]


def test_summary_lines_flag_side_ask_as_a_bookpin_deviation():
    """side_ask is execution-only, never the bar comparator — a side_ask run
    must be surfaced as a deviation from the pin, not silently rendered."""
    winners = {"S0:1": {"winner": "W", "loser": "L"}}
    rows = [_row(series_id="S0", team="W", us_side="long")]
    res = mv.evaluate_bar(rows, winners, reading="side_ask")
    lines = mv.summary_lines(res)
    assert "BOOKPIN DEVIATION" in lines[0]
    assert "execution-only" in lines[0]
    assert "[BOOKPIN: book=mid reading]" not in lines[0]
