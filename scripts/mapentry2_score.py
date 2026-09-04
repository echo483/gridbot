#!/usr/bin/env python3
"""
mapentry2_score.py — Retrospective scorer for H-MAPENTRY2 candidate aggregation.

Evaluates candidates A (v1) and B{0.0,0.10,0.20} against the frozen hypothesis
spec, producing verdict JSON to stdout.  Stdlib only.

Map winners come from the captured GRID stream: scripts/grid_winners.py emits
the first-observation verdict file (--out); its "winners" sub-dict is fed to
this scorer via the REQUIRED --winners PATH argument (keys are
"<series_id>:<map_number>").  Maps without a grid verdict are excluded with
reason "no_grid_verdict" — winners are never guessed from mapentry score
columns.  The legacy row-score oracle (resolve_map_oracle) is DEMOTED to a
post-hoc cross-check: disagreements with the grid feed are reported under
"oracle_disagreements" but never change a verdict; the grid feed alone is
authoritative.  Reported map counts only include maps that produced at least
one scored observation — see the STATUS UPDATE note below for the exact
counting semantics.

Usage:
    uv run python scripts/grid_winners.py <capture.jsonl> [...] \
        --out data/shadow/grid-winners-20260826.json
    uv run python scripts/mapentry2_score.py data/shadow/mapentry-20260826.jsonl \
        --winners data/shadow/grid-winners-20260826.json
"""

from __future__ import annotations

import argparse
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

# ── STATUS UPDATE (2026-08-26) ─────────────────────────────────────────────
# The grid_winners feed (scripts/grid_winners.py --out, a first-observation
# oracle over series_state day files) is now wired and MANDATORY: scoring
# resolves every map winner from it via the required --winners PATH argument.
# resolve_map_oracle (rows-score-derived, previously used as the scoring
# oracle) is DEMOTED to a cross-check — its "final" scores stop at the last
# qualifying CROSSING and read impossible on real capture; it must never
# decide a verdict.  Disagreements with the grid feed are reported under
# "oracle_disagreements" for auditing only (n_cross_checked reports how many
# maps were actually comparable there — 0 means "nothing to compare", not
# agreement).
#
# Counting semantics (post adversarial review):
#   * n_maps counts only maps that yielded at least one scored observation
#     on the shared candidate set, so the n >= 60 pre-registration gate can
#     never be satisfied by maps contributing zero observations, and
#     n_resolved == n_maps by construction.
#   * Map-level exclusions (no_grid_verdict / malformed_verdict /
#     null_loser / no_scoring_row) and side-level skips
#     (team_not_in_verdict) are reported in SEPARATE lists so a stray
#     team-side can never distort map-level counters.
#   * A malformed or partial winners entry is diagnosed into its own
#     exclusion reason instead of raising a bare KeyError mid-scoring.

# ── Race-13 DP ─────────────────────────────────────────────────────────────

_dp_cache: dict[int, list[list[float]]] = {}


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
    #   l=12-14 -> target 16  (period 1)
    #   l=15-17 -> target 19  (period 2)
    #   l=18-20 -> target 22  (period 3)  ...
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


def _safe_int(v: Any) -> int | None:
    """Coerce integer field, returning None on missing/bad/null values."""
    if v is None:
        return None
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    if math.isfinite(f):
        return int(f)
    return None


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
    best_row = max(rows, key=lambda r: (_safe_int(r.get("a")) or -1) + (_safe_int(r.get("b")) or -1))

    max_a = _safe_int(best_row.get("a"))
    max_b = _safe_int(best_row.get("b"))
    if max_a is None or max_b is None:
        return {
            "valid": False,
            "max_a": max_a,
            "max_b": max_b,
            "reason": "bad_score_fields",
        }
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
        # The 'team' side in best_row has more rounds -> wins
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
    with p_fut(lambda) = 0.5 + lam*((a+4)/(a+b+8) - 0.5).
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
        start = round(i * bin_size)
        end = round((i + 1) * bin_size)
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

    AMEND1: quintile everywhere - the 0.08 absolute reference, dECE criterion,
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

def _validate_verdict_entry(entry: Any) -> str | None:
    """Return a failure reason for a winners-file entry, or None if usable.

    REVIEW MAJOR#5: main() validates only that "winners" is a dict; partial or
    malformed entries used to crash later with a bare KeyError at
    winners_entry["winner"] / ["loser"].  Diagnose them up front instead.
    grid_winners.py can legitimately emit loser=None (loser.get("name") on a
    missing name), so a present-but-null loser gets its own reason too — that
    losing team-side must not be misreported as a stray third team.
    """
    if not isinstance(entry, dict):
        return "malformed_verdict"
    winner = entry.get("winner")
    if not isinstance(winner, str) or not winner:
        return "malformed_verdict"
    loser = entry.get("loser")
    if loser is None:
        return "null_loser"
    if not isinstance(loser, str) or not loser:
        return "malformed_verdict"
    return None


def _resolve_rows(
    rows: list[dict],
    winners: dict[str, dict],
) -> tuple[
    dict[tuple[str, int], list[dict]],   # by_map
    list[dict],                          # map_exclusions
    int,                                 # n_candidates_total (verdict-bearing maps)
    set[str],                            # keys blocked by malformed/null verdicts
]:
    """One grouping/validation pass shared by every candidate and the cross-check.

    Returns the map-grouped rows, the MAP-level exclusion details, the number
    of maps carrying a grid verdict, and keys whose verdict failed validation.
    """
    by_map: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for r in rows:
        sid = str(r.get("series_id", ""))
        mn = int(r.get("map_number", 0))
        by_map[(sid, mn)].append(r)

    excluded_details: list[dict] = []
    blocked_keys: set[str] = set()
    n_verdict_maps = 0

    for (sid, mn) in by_map:
        key = f"{sid}:{mn}"
        winners_entry = winners.get(key)
        if not winners_entry:
            excluded_details.append({
                "series_id": sid,
                "map_number": mn,
                "reason": "no_grid_verdict",
                # Keep the dict shape used by the legacy oracle entries.
                "max_a": None,
                "max_b": None,
            })
            continue
        reason = _validate_verdict_entry(winners_entry)
        if reason is not None:
            excluded_details.append({
                "series_id": sid,
                "map_number": mn,
                "reason": reason,
                "max_a": None,
                "max_b": None,
            })
            blocked_keys.add(key)
            continue
        n_verdict_maps += 1

    return by_map, excluded_details, n_verdict_maps, blocked_keys


def _team_sides(by_map: dict[tuple[str, int], list[dict]]) -> dict[tuple[str, int, str], list[dict]]:
    """Split each map's rows into per-team-side groups."""
    sides: dict[tuple[str, int, str], list[dict]] = defaultdict(list)
    for (sid, mn), map_rows in by_map.items():
        for r in map_rows:
            sides[(sid, mn, str(r.get("team", "")))].append(r)
    return sides


def _side_probabilities(a: int, b: int, q_round: float, p_map_rec: float | None) -> bool:
    """True iff BOTH candidate families can score this observation.

    REVIEW MAJOR#4: A uses the recorded p_map while B recomputes from
    (a, b, q_round); without a shared eligibility test the two families could
    be scored on different observation sets, silently breaking the paired
    bootstrap's pairing.  Require a finite recorded p_map AND a computable B0
    probability up front, so all candidates see exactly the same sides.
    """
    if p_map_rec is None:
        return False
    return compute_candidate_p(a, b, q_round, "B0") is not None


def resolve_map_and_side_level(
    rows: list[dict],
    winners: dict[str, dict],
) -> dict[str, Any]:
    """Single resolution pass: outcome assignment + shared probability extraction.

    Runs once over the capture rows (REVIEW MINOR#6: previously this work was
    repeated five times, once per candidate plus a redundant re-derivation of
    the counters, letting counter drift creep in).  Produces per-side records
    {prob_A, prob_B} plus level-tagged exclusion/skip lists that the pipeline
    aggregates.
    """
    by_map, map_excl, n_verdict_maps, blocked_keys = _resolve_rows(rows, winners)
    sides = _team_sides(by_map)

    skipped_team_sides: list[dict] = []
    per_side: dict[tuple[str, int, str], dict[str, float]] = {}

    for (sid, mn, team_name), team_rows in sides.items():
        key = f"{sid}:{mn}"
        if key in blocked_keys:
            continue  # already reported as a map-level exclusion
        if not winners.get(key):
            continue  # no verdict -> map-level no_grid_verdict already recorded
        winners_entry = winners[key]

        # Outcome strictly from the grid verdict (authoritative).
        # REVIEW MAJOR#5: guarded access instead of winners_entry["winner"].
        wname = winners_entry.get("winner")
        lname = winners_entry.get("loser")
        if team_name == wname:
            outcome = 1.0
        elif team_name == lname:
            outcome = 0.0
        else:
            # This team-side is neither the winner nor the loser per the
            # grid stream — never guess a side for it.  Side-level skip,
            # reported separately from map-level exclusions (MAJOR#2).
            skipped_team_sides.append({
                "series_id": sid,
                "map_number": mn,
                "reason": "team_not_in_verdict",
                "team": team_name,
            })
            continue

        # Find the LAST pre-terminal row for this team.
        # Pre-terminal: max(a,b) < 13, not (12,12); i.e., the game hasn't yet
        # reached a regulation terminal state.  REVIEW MINOR#8: coerce a/b
        # through _safe_int — r.get("a", 0) does NOT cover an explicit null
        # (build_row emits "a": None), which raised TypeError before.
        pre_term = []
        for r in team_rows:
            av = _safe_int(r.get("a"))
            bv = _safe_int(r.get("b"))
            if av is None or bv is None:
                continue
            if av >= 13 or bv >= 13:
                continue
            if av == 12 and bv == 12:
                continue
            pre_term.append((r, av, bv))
        if not pre_term:
            continue

        last_row, a, b = max(pre_term, key=lambda t: t[0].get("round_number", 0) or 0)

        q_round_s = _safe_float(last_row.get("q_round"))
        if q_round_s is None:
            continue
        p_map_rec = _safe_float(last_row.get("p_map"))

        if not _side_probabilities(a, b, q_round_s, p_map_rec):
            continue  # not scorable for the full shared candidate set

        assert p_map_rec is not None
        probs = {"A": p_map_rec}
        for c in CANDIDATE_LAMBDAS:
            pc = compute_candidate_p(a, b, q_round_s, c)
            assert pc is not None  # guaranteed by _side_probabilities for B0 path
            probs[c] = float(pc)
        per_side[(sid, mn, team_name)] = {"outcome": outcome, **probs}

    resolved_map_keys = sorted({f"{k[0]}:{k[1]}" for k in per_side})
    return {
        "per_side": per_side,
        "resolved_map_keys": resolved_map_keys,
        "n_resolved_maps": len(resolved_map_keys),
        "n_verdict_maps": n_verdict_maps,
        "excluded_details": map_excl,
        "skipped_team_sides": skipped_team_sides,
    }


def build_scoring_dataset(
    rows: list[dict],
    candidate: str,
    winners: dict[str, dict],
) -> dict[str, Any]:
    """Build series-clustered (prob, outcome) records for one candidate.

    Returns {"series_to_records": ..., "n_maps": ..., "n_excluded": ...,
             "excluded_details": [...], "skipped_team_sides": [...]}.

    `winners` is the "winners" sub-dict of a grid_winners.py --out file,
    keyed by "<series_id>:<map_number>".  It is the authoritative source of
    map winners; scoring never derives a winner from mapentry score columns.

    Per map-side: uses the LAST pre-terminal row (highest round_number where
    the game is still in progress - max(a,b) < 13 and not (12,12)).

    # AMBIGUITY: The H-MAPENTRY2 spec defines candidate computations as
    # "q at first threshold crossing" (the q_round field), but does not
    # specify at which crossing's p_map each map-side is scored.  Per the
    # implementor's instructions above, we use the LAST pre-terminal row.
    # This means the scored probability may differ from the "first crossing"
    # p_map that determined the entry.  The impact is expected to be small
    # (p_map near the terminal boundary converges toward 0/1).

    Counting (post-review semantics):
      * n_maps counts maps that yielded at least one scored observation for
        the SHARED candidate eligibility set — REVIEW MAJOR#3: counting on
        verdict presence alone let maps whose every side was dropped by the
        pre-terminal filter or null q_round/p_map satisfy the n >= 60 gate;
        that is a pre-registered PASS declared off observations that do not
        exist.  Consequently n_resolved == n_maps (REVIEW MAJOR#1: the old
        `n_maps - n_excluded` mixed disjoint populations and understated the
        corpus).
      * side-level skips are reported separately (REVIEW MAJOR#2).
    """
    resolution = resolve_map_and_side_level(rows, winners)
    per_side = resolution["per_side"]

    series_records: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for (sid, _mn, _team), rec in per_side.items():
        series_records[sid].append((rec[candidate], rec["outcome"]))

    return {
        "series_to_records": dict(series_records),
        "n_maps": resolution["n_resolved_maps"],
        "n_excluded": len(resolution["excluded_details"]),
        "excluded_details": resolution["excluded_details"],
        "skipped_team_sides": resolution["skipped_team_sides"],
    }


def cross_check_oracle(
    by_map: dict[tuple[str, int], list[dict]],
    winners: dict[str, dict],
    blocked_keys: set[str],
) -> tuple[int, list[str]]:
    """Demoted cross-check: where BOTH the grid verdict and the internal
    row-score oracle resolve a map to a winner, count and report the keys
    (sid:mn) where they disagree.

    Returns (n_compared, disagreeing_keys).  REVIEW MINOR#7: the comparison
    denominator is reported so {"count": 0} cannot be mistaken for agreement
    when the internal oracle produced nothing comparable (its scores stop at
    the last qualifying crossing, so today it resolves none of the
    verdict-matched maps).  Informational only — the grid verdict is
    authoritative and never changed by a disagreement.
    """
    disagreements: list[str] = []
    n_compared = 0
    for (sid, mn), map_rows in by_map.items():
        key = f"{sid}:{mn}"
        if key in blocked_keys:
            continue
        winners_entry = winners.get(key)
        if not winners_entry:
            continue  # no grid verdict -> nothing to cross-check
        oracle = resolve_map_oracle(map_rows)
        if not oracle.get("valid"):
            continue  # internal oracle unresolved -> no comparison possible
        n_compared += 1
        if oracle.get("winner") != winners_entry["winner"]:
            disagreements.append(key)
    return n_compared, disagreements


def run_scoring_pipeline(rows: list[dict], winners: dict[str, dict]) -> dict[str, Any]:
    """Full H-MAPENTRY2 scoring pipeline.

    Returns a result dict with JSON-serialisable fields.
    """
    by_map, _, _, blocked_keys = _resolve_rows(rows, winners)
    resolution = resolve_map_and_side_level(rows, winners)

    # Build datasets for all candidates off the SAME shared observation set.
    datasets: dict[str, Any] = {}
    for c in ["A", *CANDIDATE_LAMBDAS]:
        datasets[c] = {
            sid: [(rec[c], rec["outcome"]) for rec in recs]
            for sid, recs in _series_sided(resolution["per_side"]).items()
        }

    n_maps = resolution["n_resolved_maps"]
    exc_details = resolution["excluded_details"]
    n_excluded = len(exc_details)
    skipped = resolution["skipped_team_sides"]

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
            d_ece, ci_ece, _ = paired_bootstrap_delta(datasets["A"], datasets[c], ece_score)
            d_brier, ci_brier, _ = paired_bootstrap_delta(datasets["A"], datasets[c], brier_score)
            wins = d_ece < 0 and ci_ece[1] < 0 and d_brier < 0 and ci_brier[1] < 0
            pairwise[c] = {
                "dECE": round(d_ece, 6),
                "dECE_ci": [round(ci_ece[0], 6), round(ci_ece[1], 6)],
                "dBrier": round(d_brier, 6),
                "dBrier_ci": [round(ci_brier[0], 6), round(ci_brier[1], 6)],
                "wins": wins,
            }

        winning_lambdas = [c for c in CANDIDATE_LAMBDAS if pairwise[c].get("wins", False)]
        if winning_lambdas:
            prefer_order = ["B0", "B10", "B20"]
            if len(winning_lambdas) > 1:
                min_d = min(pairwise[w]["dECE"] for w in winning_lambdas)
                near_tied = [w for w in winning_lambdas if abs(pairwise[w]["dECE"] - min_d) <= 0.01]
                if len(near_tied) > 1:
                    chosen = min(near_tied, key=lambda w: prefer_order.index(w))
                else:
                    chosen = min(winning_lambdas, key=lambda w: prefer_order.index(w))
            else:
                chosen = winning_lambdas[0]
            verdict = chosen
        else:
            verdict = "no_challenger_wins"

    # ── Band sub-hypothesis under A ────────────────────────────────────
    band_arms: dict[str, Any] = {}
    if n_maps >= 60:
        recs_a = datasets["A"]
        band_arms["A"] = {
            "low_0.50_0.65": band_settled_edge(recs_a, 0.50, 0.65),
            "high_0.65_0.80": band_settled_edge(recs_a, 0.65, 0.801),  # upper inclusive
        }

    n_compared, disagreements = cross_check_oracle(by_map, winners, blocked_keys)

    return {
        "n_maps": n_maps,
        "n_excluded": n_excluded,
        "excluded_maps": exc_details,
        "n_resolved": n_maps,  # == n_maps by construction (see STATUS UPDATE note)
        "n_skipped_team_sides": len(skipped),
        "skipped_team_sides": skipped,
        "oracle_disagreements": {
            "count": len(disagreements),
            "maps": disagreements,
            "n_cross_checked": n_compared,
        },
        "per_candidate": per_candidate,
        "pairwise_vs_A": pairwise,
        "band_arms": band_arms,
        "verdict": verdict,
    }


def _series_sided(per_side: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for (sid, _mn, _team), rec in per_side.items():
        out[sid].append(rec)
    return out


# ── CLI entry point ────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="H-MAPENTRY2 retrospective scorer (grid_winners-fed).",
    )
    ap.add_argument("files", nargs="+", help="mapentry JSONL capture files")
    ap.add_argument(
        "--winners",
        required=True,
        help="path to grid_winners.py --out verdict JSON (the winners feed)",
    )
    args = ap.parse_args()

    try:
        with open(args.winners) as f:
            verdict_doc = json.load(f)
    except OSError as e:
        print(f"error: cannot read --winners file: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"error: invalid --winners JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(verdict_doc, dict) or not isinstance(verdict_doc.get("winners"), dict):
        print(
            "error: --winners file must be a grid_winners.py --out object "
            'with a "winners" sub-dict',
            file=sys.stderr,
        )
        sys.exit(1)

    rows = load_data(args.files)
    if not rows:
        print(json.dumps({
            "n_maps": 0,
            "error": "no_rows",
            "verdict": "insufficient_n",
        }))
        sys.exit(0)

    result = run_scoring_pipeline(rows, verdict_doc["winners"])
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

