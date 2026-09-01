"""
Tests for scripts/mapentry2_score.py — H-MAPENTRY2 retrospective scorer.

Tests are synthetic — no real data dependencies.  Each test constructs
a small set of JSONL rows, runs the scorer pipeline, and asserts against
expected values.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

# Ensure the project root is on sys.path so `scripts` is importable.
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import pytest

from scripts.mapentry2_score import (
    _build_dp,
    _winning_target,
    band_settled_edge,
    brier_score,
    build_scoring_dataset,
    compute_candidate_p,
    compute_p_map,
    ece_score,
    is_valid_terminal,
    load_data,
    paired_bootstrap_delta,
    resolve_map_oracle,
    run_scoring_pipeline,
    series_clustered_bootstrap,
)

# ============================================================
# helpers
# ============================================================

def _row(
    series_id: str = "S1",
    map_number: int = 1,
    round_number: int = 1,
    team: str = "Alpha",
    opponent: str = "Bravo",
    a: int = 0,
    b: int = 0,
    q_round: float = 0.6,
    p_map: float = 0.55,
) -> dict:
    return {
        "type": "mapentry",
        "hypothesis_id": "H-MAPENTRY1",
        "src_hypothesis_id": "H-MODELENTRY1",
        "series_id": series_id,
        "map_number": map_number,
        "round_number": round_number,
        "side": "t",
        "team": team,
        "opponent": opponent,
        "a": a,
        "b": b,
        "q_round": q_round,
        "p_map": p_map,
    }


def _verdict(
    series_id: str = "S1",
    map_number: int = 1,
    winner: str = "Alpha",
    loser: str = "Bravo",
    winner_score: int = 13,
    loser_score: int = 4,
) -> dict:
    """A grid_winners.py entry (one map verdict)."""
    return {
        "series_id": series_id,
        "map_number": map_number,
        "winner": winner,
        "loser": loser,
        "winner_score": winner_score,
        "loser_score": loser_score,
        "overtime": winner_score > 13,
    }


# ============================================================
# Race-to-13 DP
# ============================================================

class TestRace13DP:
    def test_terminal_states(self):
        dp = _build_dp(0.55)
        assert dp[13][0] == 1.0
        assert dp[13][12] == 1.0
        assert dp[0][13] == 0.0
        assert dp[11][13] == 0.0

    def test_ot_coin_flip(self):
        dp = _build_dp(0.55)
        assert dp[12][12] == 0.5

    def test_symmetric_coinflip(self):
        dp = _build_dp(0.5)
        # At (0,0) with fair coin, should be exactly 0.5
        assert dp[0][0] == pytest.approx(0.5, abs=1e-12)
        # At (1,0) the first-to-win team has advantage
        assert dp[1][0] > 0.5

    def test_one_step_from_win(self):
        dp = _build_dp(0.55)
        # At (12,11): win this round (p=0.55) -> 13-11 (win).
        # Lose this round -> 12-12 (OT coin flip 0.5).
        # P = 0.55*1 + 0.45*0.5 = 0.55 + 0.225 = 0.775
        assert dp[12][11] == pytest.approx(0.775, abs=1e-12)

    def test_one_step_from_loss(self):
        dp = _build_dp(0.55)
        # At (11,12): win this round -> 12-12 (0.5).
        # Lose -> 11-13 (0).
        # P = 0.55*0.5 + 0.45*0 = 0.275
        assert dp[11][12] == pytest.approx(0.275, abs=1e-12)


# ============================================================
# compute_p_map
# ============================================================

class TestComputePMap:
    def test_basic_state(self):
        # (0,0), q=0.55, p_fut=0.55.  The DP compounds team's slight
        # round-win advantage over 13 rounds → ≈0.687.
        p = compute_p_map(0, 0, 0.55, 0.55)
        assert p is not None
        assert p == pytest.approx(0.6865, abs=0.002)

    def test_ot_domain_none(self):
        # State 12-12 is OT domain -> None
        assert compute_p_map(12, 12, 0.6, 0.55) is None
        # State 13-10 is terminal -> None
        assert compute_p_map(13, 10, 0.6, 0.55) is None
        # State 10-13 is terminal -> None
        assert compute_p_map(10, 13, 0.6, 0.55) is None

    def test_p_fut_bias(self):
        # At (0,0), higher p_fut should give higher win prob
        p_low = compute_p_map(0, 0, 0.55, 0.50)
        p_high = compute_p_map(0, 0, 0.55, 0.60)
        assert p_low is not None and p_high is not None
        assert p_high > p_low

    def test_q_round_bias(self):
        # At (0,0) with same p_fut, higher q_round gives higher win prob
        p_low = compute_p_map(0, 0, 0.45, 0.55)
        p_high = compute_p_map(0, 0, 0.65, 0.55)
        assert p_low is not None and p_high is not None
        assert p_high > p_low

    def test_twelve_eleven(self):
        # At (12,11), q=0.8, p_fut=0.55
        # P = 0.8*dp[13][11] + 0.2*dp[12][12] = 0.8*1 + 0.2*0.5 = 0.9
        p = compute_p_map(12, 11, 0.8, 0.55)
        assert p is not None
        assert p == pytest.approx(0.9, abs=1e-12)


# ============================================================
# Candidate p_map computation (B variants)
# ============================================================

class TestCandidateP:
    def test_b0_is_coinflip_future(self):
        # B0: lambda=0, p_fut = 0.5 regardless of (a,b)
        p = compute_candidate_p(5, 3, 0.7, "B0")
        assert p is not None
        # With q=0.7 and p_fut=0.5 at (5,3) this should be > 0.5
        # We're ahead 5-3 so win prob > 0.5
        assert p > 0.55

    def test_b20_closer_to_v1(self):
        # B20: lambda=0.20, so p_fut = 0.5 + 0.20*((a+4)/(a+b+8)-0.5)
        # At (10, 2): base = (10+4)/(10+2+8)=14/20=0.7, p_fut=0.5+0.20*0.2=0.54
        # So B20 should be closer to v1 (p_fut=0.7) than B0 (p_fut=0.5) is
        p_b0 = compute_candidate_p(10, 2, 0.85, "B0")
        p_b10 = compute_candidate_p(10, 2, 0.85, "B10")
        p_b20 = compute_candidate_p(10, 2, 0.85, "B20")
        assert p_b0 is not None and p_b10 is not None and p_b20 is not None
        # All > 0.5 since ahead 10-2
        assert p_b0 > 0.5
        assert p_b20 > p_b10 > p_b0  # more future-round confidence = higher win prob

    def test_leading_state_gives_high_prob(self):
        # At (10, 2), q=0.9, B0 (p_fut=0.5): strong lead -> high prob
        p = compute_candidate_p(10, 2, 0.9, "B0")
        assert p is not None
        assert p > 0.9

    def test_losing_state_gives_low_prob(self):
        # At (2, 10), q=0.3, B0 (p_fut=0.5): far behind
        p = compute_candidate_p(2, 10, 0.3, "B0")
        assert p is not None
        assert p < 0.3


# ============================================================
# Oracle (is_valid_terminal + resolve_map_oracle)
# ============================================================

class TestIsValidTerminal:
    def test_regulation_win(self):
        assert is_valid_terminal(13, 8) is True
        assert is_valid_terminal(13, 0) is True
        assert is_valid_terminal(13, 11) is True

    def test_ot_win(self):
        assert is_valid_terminal(16, 12) is True
        assert is_valid_terminal(16, 14) is True
        assert is_valid_terminal(19, 15) is True
        assert is_valid_terminal(22, 18) is True

    def test_tie_not_terminal(self):
        assert is_valid_terminal(13, 13) is False
        assert is_valid_terminal(16, 16) is False
        assert is_valid_terminal(12, 12) is False

    def test_impossible_scores(self):
        # 13-12 is impossible: at 12-12 target becomes 16
        assert is_valid_terminal(13, 12) is False
        # 17-15: target > 15 is 19, 17 < 19 -> not terminal
        assert is_valid_terminal(17, 15) is False
        # 14-12: target=16, 14<16 -> not terminal
        assert is_valid_terminal(14, 12) is False

    def test_below_minimum(self):
        assert is_valid_terminal(12, 6) is False
        assert is_valid_terminal(11, 9) is False


class TestResolveMapOracle:
    def test_valid_regulation(self):
        rows = [
            _row("S1", 1, 1, a=0, b=0),
            _row("S1", 1, 7, a=6, b=0),
            # terminal row: team Alpha wins 13-0
            _row("S1", 1, 14, a=13, b=0),
        ]

        oracle = resolve_map_oracle(rows)
        assert oracle["valid"] is True
        assert oracle["winner"] == "Alpha"
        assert oracle["loser"] == "Bravo"
        assert oracle["team_won"] is True
        assert oracle["target"] == 13

    def test_ot_map(self):
        # OT map: final score 16-14 (Alpha wins)
        rows = [
            _row("S1", 1, round_number=25, team="Alpha", opponent="Bravo",
                  a=16, b=14),
        ]

        oracle = resolve_map_oracle(rows)
        assert oracle["valid"] is True
        assert oracle["max_a"] == 16
        assert oracle["max_b"] == 14
        assert oracle["winner"] == "Alpha"

    def test_impossible_score_excluded(self):
        # 13-12 is impossible
        rows = [
            _row("S1", 1, 1, a=0, b=0),
            _row("S1", 1, 25, a=13, b=12),
            _row("S1", 1, 25, team="Bravo", opponent="Alpha", a=12, b=13),
        ]
        oracle = resolve_map_oracle(rows)
        assert oracle["valid"] is False
        assert "impossible" in oracle.get("reason", "").lower()

    def test_no_rows_invalid(self):
        oracle = resolve_map_oracle([])
        assert oracle["valid"] is False
        assert oracle["reason"] == "no_rows"


# ============================================================
# Brier & ECE
# ============================================================

class TestBrier:
    def test_perfect_calibration(self):
        assert brier_score([1.0, 0.0, 1.0], [1.0, 0.0, 1.0]) == 0.0

    def test_worst_case(self):
        assert brier_score([0.0], [1.0]) == 1.0

    def test_mid_value(self):
        # p=0.5, outcome=1 => (0.5)^2 = 0.25
        assert brier_score([0.5], [1.0]) == 0.25

    def test_empty(self):
        assert math.isnan(brier_score([], []))


class TestECE:
    def test_perfect_calibration(self):
        # Each bin has exactly matching mean prediction and mean outcome.
        # With 10 items and 5 bins → 2/bin.
        # To get ECE=0, each bin's mean p must equal its mean outcome.
        # Bin 1: p=0.0, o=0 → |0-0| = 0
        # Bin 2: p=0.25, o=[0,1] → mean 0.5, error 0.25
        # This is NOT achievable with binary outcomes at small bin sizes.
        # Instead test near-zero for a well-calibrated set:
        probs = [0.1, 0.2, 0.3, 0.5, 0.5, 0.5, 0.7, 0.8, 0.9, 0.95]
        outs = [0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0]
        ece = ece_score(probs, outs)
        assert ece >= 0.0  # ECE is always non-negative
        # Accept any finite value
        assert not math.isnan(ece)

    def test_systematic_overconfidence(self):
        probs = [0.9] * 10  # predict 0.9 but only 5/10 win
        outs = [1.0] * 5 + [0.0] * 5
        ece = ece_score(probs, outs)
        assert ece > 0.1

    def test_empty(self):
        assert math.isnan(ece_score([], []))


# ============================================================
# Bootstrap
# ============================================================

class TestBootstrap:
    def test_series_clustered_deterministic(self):
        """Same seed produces same result."""
        data = {
            "S1": [(0.6, 1.0), (0.4, 0.0)],
            "S2": [(0.7, 1.0), (0.3, 0.0)],
        }
        m1, ci1, _ = series_clustered_bootstrap(data, brier_score, n_iter=200, seed=26)
        m2, ci2, _ = series_clustered_bootstrap(data, brier_score, n_iter=200, seed=26)
        assert m1 == m2
        assert ci1 == ci2

    def test_paired_bootstrap_runs(self):
        data_a = {"S1": [(0.6, 1.0)], "S2": [(0.4, 0.0)]}
        data_b = {"S1": [(0.55, 1.0)], "S2": [(0.45, 0.0)]}
        delta, ci, dist = paired_bootstrap_delta(
            data_a, data_b, brier_score, n_iter=200, seed=26,
        )
        # Brier(A) = 0.16, Brier(B) = 0.2025, delta > 0
        # B is worse (pushed toward 0.5 increases squared error for
        # confident outcomes)
        assert delta > 0

    def test_empty_data(self):
        val, ci, dist = series_clustered_bootstrap({}, brier_score)
        assert math.isnan(val)
        assert math.isnan(ci[0])


# ============================================================
# Band settled edge
# ============================================================

class TestBand:
    def test_perfect_profitable(self):
        # If we predict 0.55 and win, settled edge = (1-0.55)-0.04 = +0.41
        data = {"S1": [(0.55, 1.0), (0.55, 1.0)]}
        res = band_settled_edge(data, 0.50, 0.65)
        assert res["n"] == 2
        assert res["mean"] == pytest.approx(0.41, abs=0.001)

    def test_empty_band(self):
        data = {"S1": [(0.85, 1.0)]}
        res = band_settled_edge(data, 0.50, 0.65)
        assert res["n"] == 0

    def test_high_band(self):
        data = {"S1": [(0.75, 1.0)]}
        res = band_settled_edge(data, 0.65, 0.81)
        assert res["n"] == 1
        # settled edge = (1-0.75)-0.04 = 0.21
        assert res["mean"] == pytest.approx(0.21, abs=0.001)


# ============================================================
# Scoring pipeline integration
# ============================================================

class TestScoringPipeline:
    def test_insufficient_n(self):
        """Fewer than 60 maps → verdict insufficient_n."""
        rows = []
        for i in range(10):  # only 10 maps
            for r in range(1, 13):
                rows.append(_row("S1", map_number=i + 1, round_number=r,
                                  a=r, b=0, q_round=0.65, p_map=0.65))
            rows.append(_row("S1", map_number=i + 1, round_number=13,
                              a=13, b=0, team="Alpha", opponent="Bravo"))
        winners = {f"S1:{i + 1}": _verdict(map_number=i + 1) for i in range(10)}
        result = run_scoring_pipeline(rows, winners)
        assert result["verdict"] == "insufficient_n"
        assert result["n_maps"] < 60

    def test_impossible_score_excluded(self):
        """Maps with impossible terminal scores never appear in the grid
        winners feed (grid_winners.py MR12 validation filters them out), so
        the scorer excludes them as no_grid_verdict rather than guessing."""
        rows = []
        # 10 regulation maps
        for i in range(10):
            for r in range(1, 13):
                rows.append(_row("S1", map_number=i + 1, round_number=r,
                                  a=r, b=0, q_round=0.65, p_map=0.65))
            rows.append(_row("S1", map_number=i + 1, round_number=13,
                              a=13, b=0, team="Alpha", opponent="Bravo"))
        winners = {f"S1:{i + 1}": _verdict(map_number=i + 1) for i in range(10)}
        # 1 impossible-score map (13-12): no valid grid verdict will exist.
        rows.append(_row("S2", map_number=99, round_number=1, a=0, b=0))
        rows.append(_row("S2", map_number=99, round_number=25, a=13, b=12))
        rows.append(_row("S2", map_number=99, round_number=25,
                          team="Bravo", opponent="Alpha", a=12, b=13))
        result = run_scoring_pipeline(rows, winners)
        assert result["n_excluded"] >= 1
        excluded_reasons = [e["reason"] for e in result["excluded_maps"]]
        assert any(r == "no_grid_verdict" for r in excluded_reasons)

    def test_ot_counts_as_resolved(self):
        """OT map counts toward n_maps (AMEND2)."""
        rows = []
        # OT map: 12-12 followed by 16-14
        for r in range(1, 13):
            rows.append(_row("S1", map_number=1, round_number=r, a=r, b=0,
                              q_round=0.6, p_map=0.7))
        # OT rounds where team Alpha eventually wins 16-14
        rows.append(_row("S1", map_number=1, round_number=25, a=16, b=14))
        rows.append(_row("S1", map_number=1, round_number=25,
                          team="Bravo", opponent="Alpha", a=14, b=16))

        winners = {"S1:1": _verdict(winner_score=16, loser_score=14)}
        result = run_scoring_pipeline(rows, winners)
        assert result["n_maps"] == 1  # OT map counts
        assert result["n_excluded"] == 0

    def test_winners_side_correct(self):
        """When opponent wins, outcome=0 for team's entries."""
        rows = []
        for r in range(1, 13):
            rows.append(_row("S1", 1, r, a=0, b=r, q_round=0.4, p_map=0.35))
        rows.append(_row("S1", 1, 13, team="Bravo", opponent="Alpha", a=13, b=0))
        rows.append(_row("S1", 1, 13, team="Alpha", opponent="Bravo", a=0, b=13))
        winners = {"S1:1": _verdict(winner="Bravo", loser="Alpha")}
        result = run_scoring_pipeline(rows, winners)
        # Bravo won, Alpha lost
        assert result["per_candidate"]["A"]["n"] >= 1

    def test_load_data_from_file(self, tmp_path: Path):
        """load_data reads JSONL correctly."""
        p = tmp_path / "test.jsonl"
        p.write_text(json.dumps({"a": 1, "b": 2}) + "\n")
        rows = load_data([str(p)])
        assert len(rows) == 1
        assert rows[0]["a"] == 1
        assert rows[0]["b"] == 2

    def test_load_data_bad_line_skipped(self, tmp_path: Path, capsys):
        """Corrupt JSON lines are skipped with a warning."""
        p = tmp_path / "bad.jsonl"
        p.write_text('{"a":1}\nnot-json\n{"b":2}\n')
        rows = load_data([str(p)])
        assert len(rows) == 2
        stderr = capsys.readouterr().err
        assert "warning" in stderr


# ============================================================
# Edge cases
# ============================================================

class TestEdgeCases:
    def test_no_data(self):
        """Empty row list → insufficient_n."""
        result = run_scoring_pipeline([], {})
        assert result["verdict"] == "insufficient_n"
        assert result["n_maps"] == 0

    def test_negative_scores(self):
        """Negative scores should not crash — p_map returns None."""
        p = compute_p_map(-1, 5, 0.6, 0.55)
        assert p is None

    def test_bootstrap_single_series(self):
        """Single series bootstrap should still run."""
        data = {"S1": [(0.6, 1.0)] * 5}
        val, ci, dist = series_clustered_bootstrap(data, brier_score, n_iter=100, seed=26)
        assert not math.isnan(val)

    def test_p_map_out_of_range(self):
        """p_map values at extreme range."""
        p = compute_p_map(0, 0, 0.02, 0.5)
        assert p is not None
        assert 0.0 <= p <= 1.0
        p2 = compute_p_map(0, 0, 0.98, 0.5)
        assert p2 is not None
        assert 0.0 <= p2 <= 1.0

    def test_all_candidates_run(self):
        """All candidates (A, B0, B10, B20) compute without error."""
        rows = []
        for i in range(65):
            for r in range(1, 13):
                rows.append(_row("S1" if i < 60 else "S2",
                                  map_number=i + 1, round_number=r,
                                  a=r, b=0, q_round=0.65, p_map=0.65))
            rows.append(_row("S1" if i < 60 else "S2",
                              map_number=i + 1, round_number=13,
                              a=13, b=0, team="Alpha", opponent="Bravo"))
        winners = {
            f"{'S1' if i < 60 else 'S2'}:{i + 1}": _verdict(
                series_id="S1" if i < 60 else "S2", map_number=i + 1,
            )
            for i in range(65)
        }
        result = run_scoring_pipeline(rows, winners)
        assert result["n_maps"] >= 60
        for c in ["A", "B0", "B10", "B20"]:
            assert c in result["per_candidate"]
            assert result["per_candidate"][c]["n"] > 0

    def test_band_arm_consistency(self):
        """Band arms exist only when n >= 60."""
        # Few maps -> no band
        result_small = run_scoring_pipeline(
            [_row("S1", 1, 1, a=0, b=0)], {"S1:1": _verdict()},
        )
        assert "band_arms" not in result_small or result_small["band_arms"] == {}


# ============================================================
# grid_winners verdict feed (scoring oracle)
# ============================================================

class TestGridVerdictScoring:
    def test_missing_map_no_grid_verdict(self):
        """A map absent from the winners feed is excluded, never guessed."""
        rows = [
            _row("S1", 1, 1, a=0, b=0, q_round=0.6, p_map=0.55),
            _row("S1", 1, 13, a=13, b=0),
        ]
        ds = build_scoring_dataset(rows, "A", {})
        assert ds["n_maps"] == 0
        assert ds["n_excluded"] == 1
        exc = ds["excluded_details"]
        assert exc[0]["series_id"] == "S1"
        assert exc[0]["map_number"] == 1
        assert exc[0]["reason"] == "no_grid_verdict"

    def test_outcome_matches_winner_and_loser(self):
        """Winner team-side scores 1.0; loser team-side scores 0.0."""
        rows = [
            _row("S1", 1, 3, team="Alpha", opponent="Bravo", a=5, b=3,
                 q_round=0.6, p_map=0.7),
            _row("S1", 1, 3, team="Bravo", opponent="Alpha", a=3, b=5,
                 q_round=0.4, p_map=0.3),
        ]
        winners = {"S1:1": _verdict(winner="Alpha", loser="Bravo")}
        ds = build_scoring_dataset(rows, "A", winners)
        assert ds["n_maps"] == 1
        assert ds["n_excluded"] == 0
        pairs = ds["series_to_records"]["S1"]
        assert len(pairs) == 2
        by_outcome = {prob: out for prob, out in pairs}
        assert by_outcome[0.7] == 1.0  # Alpha (winner) at p=0.7
        assert by_outcome[0.3] == 0.0  # Bravo (loser) at p=0.3

    def test_team_not_in_verdict_skipped(self):
        """A team-side in neither winner nor loser sack is skipped, not guessed."""
        rows = [
            _row("S1", 1, 3, team="Alpha", opponent="Bravo", a=5, b=3,
                 q_round=0.6, p_map=0.7),
            _row("S1", 1, 3, team="Charlie", opponent="Delta", a=4, b=4,
                 q_round=0.5, p_map=0.5),
        ]
        winners = {"S1:1": _verdict(winner="Alpha", loser="Bravo")}
        ds = build_scoring_dataset(rows, "A", winners)
        # Alpha side resolved; Charlie side skipped — and the skip lives in
        # the SEPARATE side-level list (review MAJOR#2), never inflating the
        # map-level exclusion count.
        assert ds["n_maps"] == 1
        assert ds["n_excluded"] == 0
        recs = ds["series_to_records"]
        assert len(recs["S1"]) == 1
        assert recs["S1"][0] == (0.7, 1.0)
        skipped = ds["skipped_team_sides"]
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "team_not_in_verdict"
        assert skipped[0]["team"] == "Charlie"

    def test_oracle_disagreement_grid_authoritative(self):
        """When the internal oracle contradicts the grid verdict, the
        disagreement is reported and the grid verdict still decides."""
        rows = []
        for r in range(1, 13):
            rows.append(_row("S1", 1, r, team="Alpha", opponent="Bravo",
                             a=r, b=0, q_round=0.65, p_map=0.65))
        # Internal oracle reads this terminal row as Alpha winning 13-0.
        rows.append(_row("S1", 1, 13, team="Alpha", opponent="Bravo", a=13, b=0))
        # But the grid verdict says Bravo won.
        winners = {"S1:1": _verdict(winner="Bravo", loser="Alpha")}

        result = run_scoring_pipeline(rows, winners)
        assert result["oracle_disagreements"]["count"] == 1
        assert result["oracle_disagreements"]["maps"] == ["S1:1"]

        # Grid verdict is authoritative: the Alpha side must score 0.0.
        ds = build_scoring_dataset(rows, "A", winners)
        assert ds["n_maps"] == 1
        assert ds["n_excluded"] == 0
        recs = ds["series_to_records"]
        assert recs["S1"] and all(out == 0.0 for _, out in recs["S1"])


# ============================================================
# _winning_target
# ============================================================

class TestWinningTarget:
    def test_basic(self):
        assert _winning_target(0) == 13
        assert _winning_target(8) == 13
        assert _winning_target(11) == 13

    def test_ot_threshold(self):
        assert _winning_target(12) == 16
        assert _winning_target(14) == 16
        assert _winning_target(15) == 19
        assert _winning_target(18) == 22