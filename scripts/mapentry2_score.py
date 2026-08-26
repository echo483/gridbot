#!/usr/bin/env python3
"""
mapentry2_score.py — Retrospective scorer for H-MAPENTRY2 candidate aggregation.

Evaluates candidates A (v1) and B{0.0,0.10,0.20} against the frozen hypothesis
spec, producing verdict JSON to stdout.  Stdlib only.

Usage:
    uv run python scripts/mapentry2_score.py data/shadow/mapentry-20260826.jsonl
"""

from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from typing import Any

# ── constants ──────────────────────────────────────────────────────────────

# Race-to-13 DP: terminal boundary at 12-12 returns 0.5 (OT coin flip).
# Targets extend {13, 16, 19, 22, ...} for OT-aware scoring.
# AMEND2: OT-resolved maps count toward n >= 60.

CANDIDATE_LAMBDAS: dict[str, float] = {
    "B0": 0.0,
    "B10": 0.10,
    "B20": 0.20,
}

BOOTSTRAP_ITER = 2000
BOOTSTRAP_SEED = 26

# ── Race-13 DP ─────────────────────────────────────────────────────────────

_dp_cache: dict[int, list[list[float]]] = {}


# ── DRAFT STATUS (2026-08-26, Claude review of DeepSeek v4 flash output) ───
# Authored by deepseek/deepseek-v4-flash via Hermes from the frozen
# H-MAPENTRY2(+AMEND1/2) registry text; 50 tests pass; DP/ECE/bootstrap
# machinery reviewed and sound. KNOWN DEFECT (from the dispatch prompt,
# not the model): resolve_map_oracle derives "final" scores from mapentry
# rows, which stop at the last qualifying CROSSING — on real capture all
# 69 maps read as impossible_score. The registry requires winners from
# the captured GRID stream (first-observation oracle over series_state
# day files). Do NOT score verdicts with this until the oracle is fed
# from the GRID stream and candidate A is validated against the recorded
# p_map per row (both queued, STATE 7).

def _fmt_key(p: float) -> int:
    """Quantise p_fut to avoid cache explosion from FP noise."""
    return round(p * 10_000_000_000)


def _build_dp(p: float) -> list[list[float]]:
    """14x14 DP table: dp[i][j] = P(team wins) from score (i,j) with
    constant round-win prob p.  Boundary at 12-12 → 0.5 (OT)."""
    key = _fmt_key(p)
    if key in _dp_cache:
        return _dp_cache[key]
    dp = [[0.0] * 14 for _ in range(14)]
    for i in range(13, -1, -1):
        for j in range(13, -1, -1):
            if i >= 13:
                dp[i][j] = 1.0
            elif j >= 13:
                dp[i][j] = 0.0
            elif i == 12 and j == 12:
                dp[i][j] = 0.5
            else:
                dp[i][j] = p * dp[i + 1][j] + (1.0 - p) * dp[i][j + 1]
    _dp_cache[key] = dp
    return dp


def compute_p_map(a: int, b: int, q_round: float, p_fut: float) -> float | None:
    """Race-to-13 DP: current round uses q_round, future rounds use p_fut.

    Returns None when the state is in OT domain (a>=12 and b>=12) — those
    rows are skipped by the recorder and must not produce a probability.
    """
    if a < 0 or b < 0:
        return None
    if a >= 13 or b >= 13:
        return None  # terminal — game already over
    if a >= 12 and b >= 12:
        return None  # OT domain — p_map undefined per spec

    dp = _build_dp(p_fut)
    win = q_round * dp[a + 1][b] + (1.0 - q_round) * dp[a][b + 1]
    return win


# ── Oracle ─────────────────────────────────────────────────────────────────

def _winning_target(loser_score: int) -> int:
    """Smallest target in {13, 16, 19, 22, ...} > loser_score.

    OT trigger: when the trailing team reaches 12 (i.e. 12-12 tie),
    target steps to 16; at 15-15 to 19, etc.
    """
    if loser_score < 12:
        return 13
    # OT period k starts at 12 + 3k:
    #   l=12-14 → target 16  (period 1)
    #   l=15-17 → target 19  (period 2)
    #   l=18-20 → target 22  (period 3)  …
    period = (loser_score - 12) // 3 + 1
    return 13 + 3 * period


def is_valid_terminal(high: int, low: int) -> bool:
    """Check (high, low) is a valid terminal map score.

    AMEND2: OT-aware with race targets 13/16/19/22...  A score is terminal
    iff high >= smallest target > low, and high > low, and high >= 13.
    """
    if high == low:
        return False
    if high < 13:
        return False
    target = _winning_target(low)
    return high >= target


def resolve_map_oracle(rows: list[dict]) -> dict[str, Any]:
    """From all rows for one (series_id, map_number), determine terminal score
    and winner, validating against impossible-score patterns.

    Uses the row with the highest total rounds (a+b) to determine the terminal
    score, avoiding cross-row mixing of a/b values (which come from different
    team perspectives).

    Returns dict with keys:
      valid, max_a, max_b, winner, loser, team_won, target, reason
    """
    if not rows:
        return {"valid": False, "reason": "no_rows"}

    # Use the row with the highest total rounds (most advanced state).
    best_row = max(rows, key=lambda r: (r.get("a", 0) or 0) + (r.get("b", 0) or 0))

    max_a = int(best_row.get("a", 0))
    max_b = int(best_row.get("b", 0))
    high, low = (max_a, max_b) if max_a >= max_b else (max_b, max_a)

    if not is_valid_terminal(high, low):
        return {
            "valid": False,
            "max_a": max_a,
            "max_b": max_b,
            "reason": f"impossible_score:{max_a}-{max_b}",
        }

    # Determine which team won.
    if max_a > max_b:
        # The 'team' side in best_row has more rounds → wins
        winner_name = str(best_row.get("team", ""))
        loser_name = str(best_row.get("opponent", ""))
        team_won = True
    else:
        winner_name = str(best_row.get("opponent", ""))
        loser_name = str(best_row.get("team", ""))
        team_won = False

    return {
        "valid": True,
        "max_a": max_a,
        "max_b": max_b,
        "winner": winner_name,
        "loser": loser_name,
        "team_won": team_won,
        "target": _winning_target(low),
    }


# ── Data loading ───────────────────────────────────────────────────────────

def load_data(file_paths: list[str]) -> list[dict]:
    """Load mapentry JSONL rows from one or more files."""
    rows: list[dict] = []
    for path in file_paths:
        with open(path) as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(
                        f"warning: {path}:{lineno} JSON decode error: {e}",
                        file=sys.stderr,
                    )
    return rows


def _safe_float(v: Any) -> float | None:
    """Coerce numeric field, returning None on missing/bad values."""
    if v is None:
        return None
    try:
        fv = float(v)
        if math.isfinite(fv):
            return fv
    except (ValueError, TypeError):
        pass
    return None


# ── Candidate computation ──────────────────────────────────────────────────

def compute_candidate_p(
    a: int, b: int, q_round: float, candidate: str,
) -> float | None:
    """Compute p_map for a candidate.

    Candidate A: uses the recorded p_map directly (this is the actual v1 value
    that was computed at crossing time).  Candidates B{0,10,20}: race-to-13 DP
    with p_fut(λ) = 0.5 + λ·((a+4)/(a+b+8) − 0.5).
    """
    if candidate == "A":
        # A's p_map must be provided externally (recorded value).
        # This function should NOT be called for candidate A.
        raise ValueError("Candidate A uses recorded p_map, not recomputed")
    lam = CANDIDATE_LAMBDAS.get(candidate)
    if lam is None:
        raise ValueError(f"Unknown candidate: {candidate}")

    p_fut_base = (a + 4.0) / (a + b + 8.0) if (a + b) else 0.5
    p_fut = 0.5 + lam * (p_fut_base - 0.5)
    return compute_p_map(a, b, q_round, p_fut)


# ── Metrics ────────────────────────────────────────────────────────────────

def brier_score(probs: list[float], outcomes: list[float]) -> float:
    """Mean Brier score over list of probability-outcome pairs."""
    if not probs:
        return float("nan")
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)


def _quintile_bins(
    probs: list[float], outcomes: list[float], n_bins: int = 5,
) -> list[tuple[float, float, float, int]]:
    """Build n equal-count bins sorted by probability.

    Returns list of (bin_mean_pred, bin_mean_outcome, bin_error, bin_count).
    """
    pairs = sorted(zip(probs, outcomes), key=lambda x: x[0])
    total = len(pairs)
    bin_size = total / n_bins
    bins: list[tuple[float, float, float, int]] = []

    for i in range(n_bins):
        start = int(round(i * bin_size))
        end = int(round((i + 1) * bin_size))
        if end <= start:
            bin_probs = []
            bin_outs = []
        else:
            bin_slice = pairs[start:end]
            bin_probs = [p for p, _ in bin_slice]
            bin_outs = [o for _, o in bin_slice]

        if not bin_probs:
            bins.append((0.0, 0.0, 0.0, 0))
        else:
            mp = sum(bin_probs) / len(bin_probs)
            mo = sum(bin_outs) / len(bin_outs)
            bins.append((mp, mo, abs(mp - mo), len(bin_probs)))

    return bins


def ece_score(probs: list[float], outcomes: list[float], n_bins: int = 5) -> float:
    """Quintile (5 equal-count bin) Expected Calibration Error.

    AMEND1: quintile everywhere — the 0.08 absolute reference, dECE criterion,
    and reliability tables are all quintile.  (10-bin runs silently harden the
    bar and are NOT adopted.)
    """
    if not probs:
        return float("nan")
    bins = _quintile_bins(probs, outcomes, n_bins)
    n_total = sum(bc for _, _, _, bc in bins)
    if n_total == 0:
        return float("nan")
    return sum(
        bc * be for _, _, be, bc in bins
    ) / n_total


# ── Bootstrap ──────────────────────────────────────────────────────────────


def series_clustered_bootstrap(
    series_to_records: dict[str, list[tuple[float, float]]],
    metric_fn: Any,
    n_iter: int = BOOTSTRAP_ITER,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, tuple[float, float], list[float]]:
    """By-series clustered bootstrap.

    Resamples SERIES (with replacement, preserving all maps within a series).
    Returns (original_metric, (ci_lower, ci_upper), bootstrap_distribution).
    """
    series_ids = list(series_to_records.keys())
    if not series_ids:
        return (float("nan"), (float("nan"), float("nan")), [])

    rng = random.Random(seed)

    def _eval(ser_ids: list[str]) -> float:
        probs: list[float] = []
        outs: list[float] = []
        for sid in ser_ids:
            for p, o in series_to_records.get(sid, []):
                probs.append(p)
                outs.append(o)
        return metric_fn(probs, outs)

    original = _eval(series_ids)
    boot_vals: list[float] = []
    for _ in range(n_iter):
        sampled = [rng.choice(series_ids) for _ in series_ids]
        boot_vals.append(_eval(sampled))

    boot_vals.sort()
    lo = boot_vals[int(0.025 * n_iter)]
    hi = boot_vals[int(0.975 * n_iter)]
    return original, (lo, hi), boot_vals


def paired_bootstrap_delta(
    series_to_records_a: dict[str, list[tuple[float, float]]],
    series_to_records_b: dict[str, list[tuple[float, float]]],
    metric_fn: Any,
    n_iter: int = BOOTSTRAP_ITER,
    seed: int = BOOTSTRAP_SEED,
    alternative: str = "two-sided",
) -> tuple[float, tuple[float, float], list[float]]:
    """Paired by-series bootstrap of delta = metric(B) - metric(A).

    Negative delta means B improves (lower Brier / lower ECE is better).
    Returns (delta_original, (ci_lo, ci_hi), bootstrap_distribution).
    """
    all_series = set(series_to_records_a) | set(series_to_records_b)
    series_ids = sorted(all_series)
    if not series_ids:
        return (float("nan"), (float("nan"), float("nan")), [])

    rng = random.Random(seed)

    series_list_a = [
        (sid, series_to_records_a.get(sid, [])) for sid in series_ids
    ]
    series_list_b = [
        (sid, series_to_records_b.get(sid, [])) for sid in series_ids
    ]

    def _eval(
        idx_list: list[int],
    ) -> tuple[float, float]:
        pa: list[float] = []
        oa: list[float] = []
        pb: list[float] = []
        ob: list[float] = []
        for idx in idx_list:
            sid_a = series_ids[idx]
            # Always use a's prob/outcome pairs for both candidates,
            # but the prob differs per candidate
            sid_b = series_ids[idx]
            for p, o in series_to_records_a.get(sid_a, []):
                pa.append(p)
                oa.append(o)
            for p, o in series_to_records_b.get(sid_b, []):
                pb.append(p)
                ob.append(o)
        ma = metric_fn(pa, oa)
        mb = metric_fn(pb, ob)
        return ma, mb

    ma_orig, mb_orig = _eval(list(range(len(series_ids))))
    orig_delta = mb_orig - ma_orig

    boot_deltas: list[float] = []
    for _ in range(n_iter):
        sampled = [rng.randint(0, len(series_ids) - 1) for _ in series_ids]
        ma, mb = _eval(sampled)
        boot_deltas.append(mb - ma)

    boot_deltas.sort()
    lo = boot_deltas[int(0.025 * n_iter)]
    hi = boot_deltas[int(0.975 * n_iter)]
    return orig_delta, (lo, hi), boot_deltas


# ── Band sub-hypothesis ────────────────────────────────────────────────────

def band_settled_edge(
    series_to_records: dict[str, list[tuple[float, float]]],
    lo: float,
    hi: float,
    spread: float = 0.04,
) -> dict[str, Any]:
    """Settled edge for probability-outcome pairs in [lo, hi).

    Settled edge = (outcome - prob) - spread.
    Returns {n, mean, ci_lo, ci_hi} with by-series clustered CI.
    """
    series_edges: dict[str, list[float]] = defaultdict(list)
    n_total = 0
    for sid, pairs in series_to_records.items():
        for p, o in pairs:
            if lo <= p < hi:
                edge = (o - p) - spread
                series_edges[sid].append(edge)
                n_total += 1

    if n_total == 0:
        return {"n": 0, "mean": None, "ci": [None, None]}

    # Grand mean
    all_edges = [e for edges in series_edges.values() for e in edges]
    mean_edge = sum(all_edges) / len(all_edges)

    # By-series clustered bootstrap CI
    series_ids = list(series_edges.keys())
    rng = random.Random(BOOTSTRAP_SEED)
    boot_means: list[float] = []
    for _ in range(BOOTSTRAP_ITER):
        sampled_means: list[float] = []
        for _ in series_ids:
            sid = rng.choice(series_ids)
            sampled_means.extend(series_edges[sid])
        if sampled_means:
            boot_means.append(sum(sampled_means) / len(sampled_means))

    boot_means.sort()
    ci_lo = boot_means[int(0.025 * BOOTSTRAP_ITER)]
    ci_hi = boot_means[int(0.975 * BOOTSTRAP_ITER)]

    return {"n": n_total, "mean": round(mean_edge, 6), "ci": [round(ci_lo, 6), round(ci_hi, 6)]}


# ── Main scoring pipeline ──────────────────────────────────────────────────

def build_scoring_dataset(
    rows: list[dict],
    candidate: str,
) -> tuple[
    dict[str, list[tuple[float, float]]],
    int,
    int,
    list[dict],
    list[dict],
]:
    """Build series-clustered (prob, outcome) records for one candidate.

    Returns (series_to_records, n_maps, n_excluded, excluded_details, unresolved_maps).

    Per map-side: uses the LAST pre-terminal row (highest round_number where
    the game is still in progress — max(a,b) < 13 and not (12,12)).

    # AMBIGUITY: The H-MAPENTRY2 spec defines candidate computations as
    # "q at first threshold crossing" (the q_round field), but does not
    # specify at which crossing's p_map each map-side is scored.  Per the
    # implementor's instructions above, we use the LAST pre-terminal row.
    # This means the scored probability may differ from the "first crossing"
    # p_map that determined the entry.  The impact is expected to be small
    # (p_map near the terminal boundary converges toward 0/1).
    """
    # Group rows by (series_id, map_number)
    by_map: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for r in rows:
        sid = str(r.get("series_id", ""))
        mn = int(r.get("map_number", 0))
        by_map[(sid, mn)].append(r)

    # Group maps by series
    series_to_maps: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for (sid, mn) in by_map:
        series_to_maps[sid].append((sid, mn))

    excluded_details: list[dict] = []
    unresolved_maps: list[dict] = []

    # Candidate-specific records: series -> [(prob, outcome)]
    series_records: dict[str, list[tuple[float, float]]] = defaultdict(list)
    n_maps = 0

    for (sid, mn), map_rows in by_map.items():
        # Resolve winner via oracle
        oracle = resolve_map_oracle(map_rows)
        if not oracle["valid"]:
            excluded_details.append({
                "series_id": sid,
                "map_number": mn,
                "reason": oracle.get("reason", "oracle_invalid"),
                "max_a": oracle.get("max_a"),
                "max_b": oracle.get("max_b"),
            })
            continue

        n_maps += 1

        # Determine terminal state: game is over when max(a,b) >= target
        # and target > min(a,b)
        term_a = oracle["max_a"]
        term_b = oracle["max_b"]
        team_won = oracle["team_won"]

        # Group rows by team (the side that p_map predicts FOR)
        by_team: dict[str, list[dict]] = defaultdict(list)
        for r in map_rows:
            team_name = str(r.get("team", ""))
            by_team[team_name].append(r)

        map_has_prob = False
        for team_name, team_rows in by_team.items():
            # Find the LAST pre-terminal row for this team
            # Pre-terminal: max(a,b) < 13, not (12,12); i.e., the game
            # hasn't yet reached a regulation terminal state.
            pre_term = [
                r for r in team_rows
                if r.get("a", 0) < 13 and r.get("b", 0) < 13
                and not (r.get("a", 0) == 12 and r.get("b", 0) == 12)
            ]
            if not pre_term:
                continue

            # Take the highest round_number
            last_row = max(pre_term, key=lambda r: int(r.get("round_number", 0)))

            a = int(last_row.get("a", 0))
            b = int(last_row.get("b", 0))
            q_round_s = _safe_float(last_row.get("q_round"))
            if q_round_s is None:
                continue

            if candidate == "A":
                prob = _safe_float(last_row.get("p_map"))
                if prob is None:
                    continue
            else:
                prob = compute_candidate_p(a, b, q_round_s, candidate)
                if prob is None:
                    continue

            # Determine outcome: did this team win?
            a_col = int(last_row.get("a", 0))
            b_col = int(last_row.get("b", 0))
            # a is team's score, b is opponent's score in this row
            # But the max_a/max_b are over all rows.  Since team_col
            # consistently maps to the team name, we can check:
            # did this team name's total score exceed the opponent's?
            outcome = 1.0 if team_won else 0.0

            # Edge case: team and opponent may have swapped mid-map
            # (different rows have different team/opponent assignments).
            # Use the oracle which gives the definitive team_vs_opponent result.
            if team_name == oracle["winner"]:
                outcome = 1.0
            elif team_name == oracle["loser"]:
                outcome = 0.0
            # If team_name is neither (shouldn't happen), skip

            series_records[sid].append((prob, outcome))
            map_has_prob = True

        if not map_has_prob:
            # Map contributed no probability-outcome pairs (e.g., only OT
            # state entries)
            pass

    return series_records, n_maps, len(excluded_details), excluded_details, unresolved_maps


def run_scoring_pipeline(rows: list[dict]) -> dict[str, Any]:
    """Full H-MAPENTRY2 scoring pipeline.

    Returns a result dict with JSON-serialisable fields.
    """
    # Build datasets for all candidates
    datasets: dict[str, Any] = {}
    for c in ["A", *CANDIDATE_LAMBDAS]:
        recs, n_maps, n_exc, exc_details, _ = build_scoring_dataset(rows, c)
        datasets[c] = recs

    # The n_maps count is the same for all candidates (same maps, different p)
    _, n_maps, n_excluded, exc_details, _ = build_scoring_dataset(rows, "A")

    verdict = "insufficient_n" if n_maps < 60 else None

    # ── Per-candidate metrics ──────────────────────────────────────────
    per_candidate: dict[str, dict] = {}
    for c in ["A", *CANDIDATE_LAMBDAS]:
        recs = datasets[c]
        probs = [p for pairs in recs.values() for p, _ in pairs]
        outs = [o for pairs in recs.values() for _, o in pairs]
        n_obs = len(probs)

        if n_obs == 0:
            per_candidate[c] = {"n": 0, "brier": None, "ece": None}
        else:
            per_candidate[c] = {
                "n": n_obs,
                "brier": round(brier_score(probs, outs), 6),
                "ece": round(ece_score(probs, outs), 6),
            }

    # ── Pairwise: challengers vs A ─────────────────────────────────────
    pairwise: dict[str, Any] = {}
    if n_maps >= 60:
        for c in CANDIDATE_LAMBDAS:
            d_ece, ci_ece, _ = paired_bootstrap_delta(
                datasets["A"], datasets[c], ece_score,
            )
            d_brier, ci_brier, _ = paired_bootstrap_delta(
                datasets["A"], datasets[c], brier_score,
            )
            wins = (
                d_ece < 0 and ci_ece[1] < 0
                and d_brier < 0 and ci_brier[1] < 0
            )
            pairwise[c] = {
                "dECE": round(d_ece, 6),
                "dECE_ci": [round(ci_ece[0], 6), round(ci_ece[1], 6)],
                "dBrier": round(d_brier, 6),
                "dBrier_ci": [round(ci_brier[0], 6), round(ci_brier[1], 6)],
                "wins": wins,
            }

        # Determine winner with simplicity tiebreak
        winners = [c for c in CANDIDATE_LAMBDAS if pairwise[c].get("wins", False)]
        if winners:
            # Tiebreak order: B0 > B10 > B20 when |dECE| <= 0.01
            if len(winners) > 1:
                # Group by near-tie |dECE| <= 0.01
                prefer_order = ["B0", "B10", "B20"]
                # Check if all winners are near-tied
                min_d = min(pairwise[w]["dECE"] for w in winners)
                near_tied = [
                    w for w in winners
                    if abs(pairwise[w]["dECE"] - min_d) <= 0.01
                ]
                if len(near_tied) > 1:
                    chosen = min(near_tied, key=lambda w: prefer_order.index(w))
                else:
                    chosen = min(winners, key=lambda w: prefer_order.index(w))
            else:
                chosen = winners[0]
            verdict = chosen
        else:
            verdict = "no_challenger_wins"

    # ── Band sub-hypothesis under A ────────────────────────────────────
    recs_a = datasets["A"]
    band_arms: dict[str, Any] = {}
    if n_maps >= 60:
        band_arms["A"] = {
            "low_0.50_0.65": band_settled_edge(recs_a, 0.50, 0.65),
            "high_0.65_0.80": band_settled_edge(recs_a, 0.65, 0.801),  # upper inclusive
        }

    return {
        "n_maps": n_maps,
        "n_excluded": n_excluded,
        "excluded_maps": exc_details,
        "n_resolved": n_maps - n_excluded,
        "per_candidate": per_candidate,
        "pairwise_vs_A": pairwise,
        "band_arms": band_arms,
        "verdict": verdict,
    }


# ── CLI entry point ────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <mapentry.jsonl> [...]", file=sys.stderr)
        sys.exit(1)

    rows = load_data(sys.argv[1:])
    if not rows:
        print(json.dumps({
            "n_maps": 0,
            "error": "no_rows",
            "verdict": "insufficient_n",
        }))
        sys.exit(0)

    result = run_scoring_pipeline(rows)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()