"""Tests for scripts.entry_quality (settled would-trade entry quality strata).

Analysis only (slow loop, iron rail 1): locks in the engine dedup mirror (first
qualifying row per (series, map, slug, side)), the frozen entry-price rule
(min(side_ask - 0.01, entry_price_max, 0.90)), the winner-file-only settled
outcome, the shock_pre window semantics, and the strata shape.  No network, no
keyring, no fixtures on disk — every input is a synthetic dict built inline;
file-based behaviour is exercised through tmp_path writers and CLI subprocesses
(mirroring test_wave_edge_join.py).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure the project root is on sys.path so `scripts` is importable (mirror
# the existing test_wave_edge_join.py).
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from scripts import entry_quality as eq

NS = 1_000_000_000


# ── synthetic-input builders ────────────────────────────────────────────────

def mk_row(ts_ns=NS, series_id="s1", map_number=1, round_number=2,
           team="VA", opponent="5s", p_map=0.7803,
           market_slug="astatc-cs2-va-5s-2026-08-27-map1", us_side="long",
           bid=0.55, ask=0.58, side_ask=0.58, entry_price_max=0.7403,
           qualifies=True, **kw) -> dict:
    """A qualifying mapentry row shaped like the live capture producer."""
    row = {
        "type": "mapentry",
        "series_id": series_id,
        "map_number": map_number,
        "round_number": round_number,
        "team": team,
        "opponent": opponent,
        "q_round": 0.71,
        "p_map": p_map,
        "market_slug": market_slug,
        "us_side": us_side,
        "bid": bid,
        "ask": ask,
        "side_ask": side_ask,
        "entry_price_max": entry_price_max,
        "qualifies": qualifies,
        "ts_ns": ts_ns,
    }
    row.update(kw)
    return row


def mk_winners(*specs) -> dict:
    """A grid_winners --out document from (series_id, map, winner, loser) specs."""
    return {
        "winners": {
            f"{s}:{m}": {"winner": w, "loser": l} for s, m, w, l in specs
        }
    }


def settle(rows, winners):
    """Run the pure pipeline: gate -> dedup -> settle.  Returns (cands, evals,
    merged reason counts).  `winners` may be the full grid_winners document or
    the flattened by-key mapping."""
    cands, reasons, _ = eq.select_candidates(rows)
    if isinstance(winners, dict) and isinstance(winners.get("winners"), dict):
        winners = winners["winners"]
    evals, settle_reasons = eq.settle_candidates(cands, winners)
    return cands, evals, {**reasons, **settle_reasons}


VA_WIN = ("s1", 1, "VA", "5s")


# ── (a) discount bucketing, including the exactly-10.0 boundary ─────────────

def test_discount_bucket_boundaries():
    assert eq.discount_bucket(-3.0) == "lt5"        # negatives never crash
    assert eq.discount_bucket(4.999) == "lt5"
    assert eq.discount_bucket(5.0) == "5to10"
    assert eq.discount_bucket(9.999) == "5to10"
    assert eq.discount_bucket(10.0) == "10to20"     # spec boundary
    assert eq.discount_bucket(19.999) == "10to20"
    assert eq.discount_bucket(20.0) == "gte20"
    assert eq.discount_bucket(120.0) == "gte20"


def test_discount_pts_rounding_pins_exact_ten():
    # (0.70 - 0.60) * 100 is 9.999999999999998 in binary floats; the 6dp
    # rounding must pin it to exactly 10.0 so the boundary is reproducible.
    assert eq.discount_pts(0.70, 0.60) == 10.0
    assert eq.discount_bucket(eq.discount_pts(0.70, 0.60)) == "10to20"


def test_discount_pipeline_boundary_row_lands_10to20():
    # p_map 0.70, ticked price 0.61-0.01=0.60 -> exactly 10.0 pts -> "10to20".
    row = mk_row(ts_ns=NS, p_map=0.70, side_ask=0.61, entry_price_max=0.66)
    cands, evals, reasons = settle([row], mk_winners(VA_WIN))
    # unparseable_lines is the loader's counter (folded in by main()); the
    # pipeline-level reason dicts carry the other five.
    assert reasons == {
        "malformed_row": 0, "entry_price_out_of_range": 0,
        "duplicate_key": 0, "no_verdict": 0, "team_not_in_verdict": 0,
    }
    assert cands[0]["entry_price"] == 0.60
    strata = eq.compute_strata(evals)
    assert strata["discount"]["10to20"]["n"] == 1
    assert strata["discount"]["10to20"]["mean_edge"] == pytest.approx(
        1.0 - 0.60)


# ── (b) shock_pre semantics: 600s window boundary, both flags ───────────────

def test_shock_window_boundary_inclusive_at_600s():
    # Previous row exactly at ts-600s: inside the inclusive window -> counts.
    seq = [(100 * NS, 0.40), (700 * NS, 0.70)]
    assert eq.has_pre_shock(seq, 1, 0.25, 600 * NS) is True
    # One nanosecond earlier: outside the window -> does not count.
    seq_early = [(100 * NS - 1, 0.40), (700 * NS, 0.70)]
    assert eq.has_pre_shock(seq_early, 1, 0.25, 600 * NS) is False


def test_shock_pair_below_threshold_is_false():
    seq = [(100 * NS, 0.50), (700 * NS, 0.70)]  # delta 0.20 < 0.25
    assert eq.has_pre_shock(seq, 1, 0.25, 600 * NS) is False


def test_shock_jump_into_candidate_row_counts():
    # The candidate is the second element of the jumping pair: that jump is
    # realized strictly before the entry decision, so shock_pre is True.
    seq = [(0, 0.40), (200 * NS, 0.70)]
    assert eq.has_pre_shock(seq, 1, 0.25, 600 * NS) is True


def test_old_jump_not_visible_after_window_slides_past():
    seq = [(0, 0.40), (200 * NS, 0.70), (650 * NS, 0.70)]
    # For the 650s row the 0->200s jump has its first element outside the
    # window [50s, 650s]; the in-window pair is flat.
    assert eq.has_pre_shock(seq, 2, 0.25, 600 * NS) is False
    # ...but for the 200s row the same jump is fully in-window.
    assert eq.has_pre_shock(seq, 1, 0.25, 600 * NS) is True


def test_shock_pre_pipeline_true_and_false():
    rows = [
        # s1: jump 0.40 -> 0.70 lands directly on the qualifying row.
        mk_row(ts_ns=0, team="VA", p_map=0.40, qualifies=False),
        mk_row(ts_ns=200 * NS, team="VA", p_map=0.70, side_ask=0.61,
               entry_price_max=0.66, market_slug="m1"),
        # s2: same jump, but the qualifying row sits 650s out -- the jump's
        # first element has slid out of the candidate's 600s window.
        mk_row(ts_ns=0, series_id="s2", team="VA", p_map=0.40,
               qualifies=False),
        mk_row(ts_ns=650 * NS, series_id="s2", team="VA", p_map=0.70,
               side_ask=0.61, entry_price_max=0.66, market_slug="m2"),
    ]
    winners = mk_winners(VA_WIN, ("s2", 1, "VA", "5s"))
    cands, evals, reasons = settle(rows, winners)
    assert reasons["malformed_row"] == 0 and len(cands) == 2
    flags = {c["series_id"]: c["shock_pre"] for c in cands}
    assert flags == {"s1": True, "s2": False}
    strata = eq.compute_strata(evals)
    assert strata["shock_pre"]["true"]["n"] == 1
    assert strata["shock_pre"]["false"]["n"] == 1


# ── (c) phase boundaries ────────────────────────────────────────────────────

def test_phase_bucket_boundaries():
    assert eq.phase_bucket(1) == "early"
    assert eq.phase_bucket(5) == "early"     # r5 early (spec boundary)
    assert eq.phase_bucket(6) == "mid"       # r6 mid
    assert eq.phase_bucket(15) == "mid"
    assert eq.phase_bucket(16) == "late"     # r16 late


def test_phase_pipeline_boundaries():
    rows = [
        mk_row(ts_ns=1 * NS, series_id="sa", round_number=5, market_slug="ma"),
        mk_row(ts_ns=2 * NS, series_id="sb", round_number=6, market_slug="mb"),
        mk_row(ts_ns=3 * NS, series_id="sc", round_number=16, market_slug="mc"),
    ]
    winners = mk_winners(("sa", 1, "VA", "5s"), ("sb", 1, "VA", "5s"),
                         ("sc", 1, "VA", "5s"))
    _, evals, _ = settle(rows, winners)
    assert len(evals) == 3
    strata = eq.compute_strata(evals)
    assert strata["phase"]["early"]["n"] == 1
    assert strata["phase"]["mid"]["n"] == 1
    assert strata["phase"]["late"]["n"] == 1


# ── (d) dedup: first qualifying row only, exactly once per key ──────────────

def test_dedup_keeps_first_qualifying_row():
    rows = [
        mk_row(ts_ns=1 * NS, round_number=2, p_map=0.60, side_ask=0.55,
               entry_price_max=0.56),
        mk_row(ts_ns=8 * NS, round_number=8, p_map=0.80, side_ask=0.60,
               entry_price_max=0.76),
    ]
    cands, evals, reasons = settle(rows, mk_winners(VA_WIN))
    assert len(cands) == 1 and len(evals) == 1
    assert cands[0]["round_number"] == 2          # the FIRST row's identity
    assert cands[0]["ts_ns"] == 1 * NS
    assert cands[0]["p_map"] == 0.60              # and the FIRST row's fields
    assert cands[0]["entry_price"] == 0.54        # min(0.55-0.01, 0.56, 0.90)
    assert reasons["duplicate_key"] == 1
    assert reasons["malformed_row"] == 0


def test_dedup_runs_before_verdict_lookup():
    # The engine dedupes at intent time, before outcomes exist: the first
    # qualifying row consumes the key even though its verdict is missing, and
    # the later row is a duplicate -- both are counted, neither settles.
    rows = [
        mk_row(ts_ns=1 * NS, series_id="sx"),
        mk_row(ts_ns=2 * NS, series_id="sx", round_number=7),
    ]
    cands, evals, reasons = settle(rows, {"winners": {}})
    assert len(cands) == 1 and evals == []   # gated in; settled nowhere
    assert reasons["no_verdict"] == 1
    assert reasons["duplicate_key"] == 1


def test_dedup_key_includes_slug_and_side():
    # Same (series, map) but different books (slug x side): both survive.
    rows = [
        mk_row(ts_ns=1 * NS, us_side="long", market_slug="m-long"),
        mk_row(ts_ns=2 * NS, us_side="short", market_slug="m-long"),
        mk_row(ts_ns=3 * NS, us_side="long", market_slug="m-long-b"),
    ]
    cands, _, reasons = settle(rows, mk_winners(VA_WIN))
    assert len(cands) == 3
    assert reasons["duplicate_key"] == 0


# ── (e) missing verdict excluded but counted; team never guessed ────────────

def test_missing_verdict_excluded_but_counted():
    cands, evals, reasons = settle([mk_row(ts_ns=NS)], {"winners": {}})
    assert len(cands) == 1        # gated into the population
    assert evals == []            # but settled nowhere
    assert reasons["no_verdict"] == 1


def test_team_not_in_verdict_never_guessed():
    # Verdict names that match neither slot: no score or ordering inference.
    winners = mk_winners(("s1", 1, "Alpha", "Beta"))
    _, evals, reasons = settle([mk_row(ts_ns=NS, team="VA")], winners)
    assert evals == []
    assert reasons["team_not_in_verdict"] == 1


def test_won_and_lost_settle_by_exact_name():
    rows = [
        mk_row(ts_ns=1 * NS, series_id="s1"),
        mk_row(ts_ns=2 * NS, series_id="s2", market_slug="m2"),
    ]
    winners = mk_winners(("s1", 1, "VA", "5s"),   # row team wins
                         ("s2", 1, "5s", "VA"))   # row team loses
    _, evals, reasons = settle(rows, winners)
    assert reasons["no_verdict"] == 0 and reasons["team_not_in_verdict"] == 0
    assert [e["won"] for e in evals] == [1.0, 0.0]
    # settled edge = won - entry_price; row2 entry 0.57 -> -0.57
    assert evals[0]["edge"] == pytest.approx(1.0 - 0.57)
    assert evals[1]["edge"] == pytest.approx(-0.57)


# ── (f) entry price: ceiling 0.90 binds; out-of-range excluded ──────────────

def test_entry_ceiling_binds():
    assert eq.entry_price(0.95, 0.94) == 0.90      # ceiling < both others
    assert eq.entry_price(0.58, 0.7403) == 0.57    # the live sample row


def test_entry_ceiling_pipeline():
    row = mk_row(ts_ns=NS, p_map=0.95, side_ask=0.95, entry_price_max=0.94)
    cands, _, reasons = settle([row], mk_winners(VA_WIN))
    assert reasons["entry_price_out_of_range"] == 0
    assert cands[0]["entry_price"] == 0.90         # clamped down to the ceiling


def test_entry_price_out_of_range_excluded_and_counted():
    # A degenerate price (<= 0) is one the engine would never build; the row is
    # excluded and does not consume its dedup key.
    row = mk_row(ts_ns=NS, p_map=0.10, side_ask=0.05, entry_price_max=-0.02)
    cands, _, reasons = settle([row], mk_winners(VA_WIN))
    assert cands == []
    assert reasons["entry_price_out_of_range"] == 1


def test_entry_price_below_one_tick_excluded():
    # side_ask - 0.01 <= 0: no orderable price.
    row = mk_row(ts_ns=NS, p_map=0.20, side_ask=0.005, entry_price_max=0.16)
    cands, _, reasons = settle([row], mk_winners(VA_WIN))
    assert cands == []
    assert reasons["entry_price_out_of_range"] == 1


# ── (g) malformed rows: skipped + counted, never crashing ───────────────────

def test_malformed_rows_skipped_and_counted(tmp_path):
    giant = 10 ** 400  # JSON int that overflows float(): must not crash

    def mutated(base_ts, **over):
        """A good row with one field deliberately corrupted (dict-level, so the
        corruption bypasses mk_row's typed parameters)."""
        row = mk_row(ts_ns=base_ts)
        row.update(over)
        return row

    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        json.dumps(mk_row(ts_ns=1 * NS)) + "\n"       # the one good row
        + "{broken json\n"                             # decode error
        + "[1, 2, 3]\n"                                # non-object line
        + "\n"                                         # blank: silent
        + "# capture annotation\n"                     # comment: silent
        + "{# stub\n"                                  # jq stub: silent
        + json.dumps({                                 # missing team
            "type": "mapentry", "series_id": "s1", "map_number": 1,
            "round_number": 2, "qualifies": True, "market_slug": "m",
            "us_side": "long", "side_ask": 0.58, "entry_price_max": 0.74,
            "p_map": 0.78, "ts_ns": 2 * NS}) + "\n"
        + json.dumps(mutated(3 * NS, ts_ns=3.5)) + "\n"         # float ts_ns
        + json.dumps(mutated(4 * NS, p_map="high")) + "\n"
        + json.dumps(mutated(5 * NS, p_map=giant)) + "\n"       # OverflowError
        + json.dumps(mutated(6 * NS, us_side=3)) + "\n"         # bad side
        + json.dumps(mutated(7 * NS, qualifies=1)) + "\n"       # not True
        + json.dumps({"type": "ss", "data": {}}) + "\n",        # out of scope
        encoding="utf-8",
    )
    rows, problems = eq.load_mapentry(str(bad))
    assert len(problems) == 2          # decode error + non-object line
    cands, reasons, counters = eq.select_candidates(rows)
    assert len(cands) == 1 and cands[0]["ts_ns"] == 1 * NS
    assert reasons["malformed_row"] == 5   # no-team, float-ts, str-p, giant-p, int-side
    assert counters["non_qualifying_rows"] == 1   # qualifies=1 is not True
    assert counters["out_of_scope_type"] == 1     # the ss row
    assert counters["rows_loaded"] == len(rows)


def test_unhashable_containers_do_not_crash():
    # Lists/dicts in identity or numeric slots must take the malformed path,
    # never raise TypeError from a dict-key probe.
    rows = [
        {**mk_row(ts_ns=NS), "series_id": ["s1"]},
        {**mk_row(ts_ns=NS), "map_number": {"a": 1}},
        {**mk_row(ts_ns=NS), "p_map": [0.5]},
        {**mk_row(ts_ns=NS), "market_slug": {"slug": 1}},
    ]
    cands, reasons, _ = eq.select_candidates(rows)
    assert cands == []
    assert reasons["malformed_row"] == 4


def test_non_dict_rows_ignored_by_pipeline():
    cands, reasons, counters = eq.select_candidates(["garbage", 7, None])
    assert cands == []
    assert reasons["malformed_row"] == 0   # loader's problem list, not ours
    assert counters["rows_loaded"] == 0


# ── strata shape: empty strata, fixed keys, reconciliation ──────────────────

def test_empty_strata_all_zero_null():
    strata = eq.compute_strata([])
    assert set(strata) == {"discount", "p_map", "phase", "shock_pre", "side",
                           "band", "discount_x_phase"}
    for group, keys in (
        ("discount", ("lt5", "5to10", "10to20", "gte20")),
        ("p_map", ("0.40-0.55", "0.55-0.70", "0.70-0.80", "0.80-0.90",
                   "outside")),
        ("phase", ("early", "mid", "late")),
        ("shock_pre", ("true", "false")),
        ("side", ("long", "short")),
        ("band", ("core", "shoulder", "outside")),
    ):
        assert set(strata[group]) == set(keys), group
        for k, m in strata[group].items():
            assert m == {"n": 0, "win_rate": None, "mean_edge": None,
                         "total_edge": None}, (group, k)
    for d in ("lt5", "5to10", "10to20", "gte20"):
        assert set(strata["discount_x_phase"][d]) == {"early", "mid", "late"}
        for m in strata["discount_x_phase"][d].values():
            assert m["n"] == 0 and m["win_rate"] is None


def test_strata_buckets_reconcile_to_n():
    rows = [
        mk_row(ts_ns=1 * NS, p_map=0.70, side_ask=0.61, entry_price_max=0.66),
        mk_row(ts_ns=2 * NS, series_id="s2", p_map=0.50, side_ask=0.48,
               entry_price_max=0.46, market_slug="m2"),
        mk_row(ts_ns=3 * NS, series_id="s3", p_map=0.85, side_ask=0.82,
               entry_price_max=0.81, market_slug="m3", us_side="short"),
    ]
    winners = mk_winners(("s1", 1, "VA", "5s"), ("s2", 1, "5s", "VA"),
                         ("s3", 1, "VA", "5s"))
    _, evals, _ = settle(rows, winners)
    assert len(evals) == 3
    strata = eq.compute_strata(evals)
    for group in ("discount", "p_map", "phase", "shock_pre", "side", "band"):
        assert sum(strata[group][k]["n"] for k in strata[group]) == 3, group
    nested_total = sum(
        strata["discount_x_phase"][d][p]["n"]
        for d in strata["discount_x_phase"]
        for p in strata["discount_x_phase"][d]
    )
    assert nested_total == 3
    # side split follows us_side
    assert strata["side"]["long"]["n"] == 2
    assert strata["side"]["short"]["n"] == 1


def test_p_map_and_band_bucket_boundaries():
    assert eq.p_map_bucket(0.39) == "outside"
    assert eq.p_map_bucket(0.40) == "0.40-0.55"
    assert eq.p_map_bucket(0.55) == "0.55-0.70"
    assert eq.p_map_bucket(0.70) == "0.70-0.80"
    assert eq.p_map_bucket(0.80) == "0.80-0.90"
    assert eq.p_map_bucket(0.90) == "0.80-0.90"
    assert eq.p_map_bucket(0.91) == "outside"
    assert eq.band_bucket(0.50) == "core"
    assert eq.band_bucket(0.80) == "core"
    assert eq.band_bucket(0.81) == "shoulder"
    assert eq.band_bucket(0.40) == "shoulder"
    assert eq.band_bucket(0.90) == "shoulder"
    assert eq.band_bucket(0.39) == "outside"


# ── CLI: stdout JSON, ONE stderr line, --fills tagging, --out parity ────────

def _write_inputs(tmp_path):
    rows = [
        mk_row(ts_ns=1 * NS, p_map=0.70, side_ask=0.61, entry_price_max=0.66),
        mk_row(ts_ns=2 * NS, series_id="s2", p_map=0.60, side_ask=0.55,
               entry_price_max=0.56, market_slug="bs-cs2-xx-yy-map1",
               us_side="short", round_number=9, team="XX"),
    ]
    mapentry = tmp_path / "mapentry.jsonl"
    mapentry.write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    winners = tmp_path / "winners.json"
    winners.write_text(json.dumps(mk_winners(
        ("s1", 1, "VA", "5s"),        # row1 wins
        ("s2", 1, "GG", "XX"),        # row2 loses
    )), encoding="utf-8")
    return str(mapentry), str(winners), rows


def _run_cli(tmp_path, *extra):
    mapentry, winners, rows = _write_inputs(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-X", "utf8",
         str(_project_root / "scripts" / "entry_quality.py"),
         mapentry, "--winners", winners, *extra],
        capture_output=True, text=True, timeout=120,
    )
    return proc, mapentry, winners, rows


def test_cli_stdout_json_stderr_one_line(tmp_path):
    proc, *_ = _run_cli(tmp_path)
    assert proc.returncode == 0
    doc = json.loads(proc.stdout)
    assert set(doc) == {"n", "excluded", "strata", "fills_subset", "params"}
    assert doc["n"] == 2
    assert doc["fills_subset"] is None
    assert set(doc["excluded"]) == {
        "unparseable_lines", "malformed_row", "entry_price_out_of_range",
        "duplicate_key", "no_verdict", "team_not_in_verdict"}
    assert sum(doc["excluded"].values()) == 0
    assert sum(doc["strata"]["discount"][k]["n"]
               for k in doc["strata"]["discount"]) == 2
    # ONE-line stderr summary contract: every non-empty line carries the prefix.
    err_lines = [ln for ln in proc.stderr.splitlines() if ln.strip()]
    assert len(err_lines) == 1
    assert err_lines[0].startswith("entry_quality:")
    assert "settled=2" in err_lines[0]


def test_cli_fills_tagging_subset(tmp_path):
    fills = tmp_path / "fills.json"
    fills.write_text(json.dumps([
        {"market_slug": "astatc-cs2-va-5s-2026-08-27-map1",
         "us_side": "long", "order_id": "ord-1"},
    ]), encoding="utf-8")
    proc, *_ = _run_cli(tmp_path, "--fills", str(fills))
    assert proc.returncode == 0
    doc = json.loads(proc.stdout)
    assert doc["n"] == 2                       # would-trade population
    assert doc["fills_subset"]["n"] == 1       # tagged actual fill
    sub = doc["fills_subset"]["strata"]
    assert set(sub) == set(doc["strata"])      # same strata shape
    assert sum(sub["discount"][k]["n"] for k in sub["discount"]) == 1
    # The tagged fill is the long VA row (won): edge 1.0 - 0.60 = 0.40.
    tagged_bucket = next(m for m in sub["discount"].values() if m["n"] == 1)
    assert tagged_bucket["win_rate"] == pytest.approx(1.0)
    assert tagged_bucket["mean_edge"] == pytest.approx(0.40)
    assert doc["params"]["fills_loaded_pairs"] == 1
    assert doc["params"]["fills_skipped"] == 0


def test_cli_fills_file_accepts_wrapped_list_and_counts_skips(tmp_path):
    fills = tmp_path / "fills.json"
    fills.write_text(json.dumps({"fills": [
        {"market_slug": "astatc-cs2-va-5s-2026-08-27-map1",
         "us_side": "long", "order_id": "ord-1"},
        {"market_slug": "astatc-cs2-va-5s-2026-08-27-map1", "us_side": "x"},
        42,
    ]}), encoding="utf-8")
    proc, *_ = _run_cli(tmp_path, "--fills", str(fills))
    assert proc.returncode == 0
    doc = json.loads(proc.stdout)
    assert doc["fills_subset"]["n"] == 1
    assert doc["params"]["fills_skipped"] == 2


def test_cli_out_file_matches_stdout(tmp_path):
    out_file = tmp_path / "entry_quality_out.json"
    proc, mapentry, winners, _ = _run_cli(tmp_path)
    assert proc.returncode == 0
    run2 = subprocess.run(
        [sys.executable, "-X", "utf8",
         str(_project_root / "scripts" / "entry_quality.py"),
         mapentry, "--winners", winners, "--out", str(out_file)],
        capture_output=True, text=True, timeout=120,
    )
    assert run2.returncode == 0
    assert json.loads(out_file.read_text(encoding="utf-8")) == \
        json.loads(run2.stdout)


def test_cli_shock_params_flow_through(tmp_path):
    # A tighter window flips the s1 candidate to shock_pre=false.
    proc, *_ = _run_cli(tmp_path, "--shock", "0.25", "--shock-window-s", "100")
    assert proc.returncode == 0
    doc = json.loads(proc.stdout)
    assert doc["params"]["shock"] == 0.25
    assert doc["params"]["shock_window_s"] == 100
    # Neither CLI row follows a >=0.25 jump within 100s -> all false.
    assert doc["strata"]["shock_pre"]["false"]["n"] == 2


def test_cli_missing_winners_file_fails_cleanly(tmp_path):
    proc, mapentry, _, _ = _run_cli(tmp_path)
    proc_bad = subprocess.run(
        [sys.executable, "-X", "utf8",
         str(_project_root / "scripts" / "entry_quality.py"),
         mapentry, "--winners", str(tmp_path / "nope.json")],
        capture_output=True, text=True, timeout=120,
    )
    assert proc_bad.returncode == 1
    assert "entry_quality" in proc_bad.stderr
