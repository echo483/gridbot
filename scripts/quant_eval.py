"""Quant evaluation harness: calibration + EV + bankroll risk, offline.

Slow loop ONLY (iron rail 1): deterministic, read-only over resolved shadow
instances (resolution borrowed from gridbot.instruments.verdict so the cells
here are the same cells the registry verdicts use). Three layers:

  1. Calibration — per-cell Brier (with skill vs the base-rate forecast) and
     equal-count-bin ECE of the recorded fair_value against round outcomes.
  2. EV — per-contract expectation at two entry prices: the frozen ceiling
     (stress floor: worst price the engine may ever pay) and the designed
     margin fair_value-0.04 (what the maker path targets), each with the
     Wilson-lower-bound conservative variant, plus the Kelly fraction.
  3. Bankroll risk — seeded Monte Carlo (flat $1 clips and quarter-Kelly,
     with Beta parameter uncertainty over the true win rate) and closed-form
     drifted-Brownian barrier math: P(double before halve) and expected
     trades to either barrier.

The registry bars remain the only PASS/FAIL decision rule; this harness is
the reporting layer they reference (H-MAPENTRY1). Nothing here touches
orders, keys, or the network.

Run: uv run python scripts/quant_eval.py [--json] [--trades N] [--paths N]
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict
from datetime import UTC, datetime

from gridbot import config
from gridbot.executor.intents import entry_ceiling
from gridbot.instruments import verdict

BANKROLL0 = 26.0  # verified account balance at first fire, 2026-08-25
MARGIN = 0.04  # designed entry margin below fair value (H-MAPENTRY1 parity)
SEED = 13


# ---------------------------------------------------------------- calibration


def brier(pairs: list[tuple[float, float]]) -> float:
    """Mean squared error of probability forecasts: pairs of (p, outcome)."""
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def brier_skill(pairs: list[tuple[float, float]]) -> float | None:
    """1 - Brier/Brier_ref where the reference forecast is the base rate.

    For a constant forecast at the pooled outcome rate p̄ the Brier score is
    exactly p̄(1-p̄); skill > 0 means the recorded fair values carry real
    information beyond "always predict the average".
    """
    base = sum(y for _, y in pairs) / len(pairs)
    ref = base * (1 - base)
    if ref == 0:
        return None
    return 1 - brier(pairs) / ref


def ece(pairs: list[tuple[float, float]], bins: int = 5) -> float:
    """Expected calibration error over equal-COUNT bins (adaptive binning).

    Equal-width bins go empty when forecasts concentrate (ours live in
    0.65-0.97), so bins are quantile slices of the sorted forecasts.
    """
    ordered = sorted(pairs)
    n = len(ordered)
    k = min(bins, n)
    total = 0.0
    for i in range(k):
        lo, hi = i * n // k, (i + 1) * n // k
        chunk = ordered[lo:hi]
        if not chunk:
            continue
        mean_p = sum(p for p, _ in chunk) / len(chunk)
        rate = sum(y for _, y in chunk) / len(chunk)
        total += abs(mean_p - rate) * len(chunk)
    return total / n


def wilson(k: int, n: int) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion."""
    if n == 0:
        return 0.0, 0.0
    z = 1.96
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return center - half, center + half


# ------------------------------------------------------------------------- EV


def ev_per_contract(p: float, price: float) -> float:
    """Expected profit per $1-payout contract bought at `price`."""
    return p - price


def kelly_fraction(p: float, price: float) -> float:
    """Optimal bankroll fraction for a binary contract at `price`, floored 0.

    Net odds are b = (1-price)/price; f* = (pb - (1-p))/b.
    """
    if not 0 < price < 1:
        return 0.0
    b = (1 - price) / price
    return max(0.0, (p * b - (1 - p)) / b)


# ------------------------------------------------------- Brownian barrier math


def barrier_up_probability(mu: float, sigma: float, up: float, down: float) -> float:
    """P(drifted Brownian motion hits +up before -down).

    X_t = mu*t + sigma*W_t; with theta = 2*mu/sigma^2, exp(-theta*X) is a
    martingale, giving P_up = (e^{theta*down} - 1)/(e^{theta*down} - e^{-theta*up}).
    Driftless limit: down/(up+down).
    """
    if sigma <= 0:
        return 1.0 if mu > 0 else 0.0
    theta = 2 * mu / (sigma * sigma)
    if abs(theta) < 1e-12:
        return down / (up + down)
    # Rescale to avoid overflow for strong drift.
    e_down = math.exp(min(theta * down, 700))
    e_up = math.exp(min(-theta * up, 700))
    return (e_down - 1) / (e_down - e_up)


def expected_trades_to_barrier(mu: float, sigma: float, up: float, down: float) -> float | None:
    """Wald identity: E[T] = (P_up*up - (1-P_up)*down)/mu (driftless: up*down/sigma^2)."""
    if sigma <= 0 and mu == 0:
        return None
    if abs(mu) < 1e-12:
        return up * down / (sigma * sigma)
    p_up = barrier_up_probability(mu, sigma, up, down)
    return (p_up * up - (1 - p_up) * down) / mu


def flat_stake_stats(p: float, price: float, stake: float) -> tuple[float, float]:
    """(mean, std) of per-trade dollar PnL for a flat `stake` at `price`."""
    b = (1 - price) / price
    win, loss = stake * b, -stake
    mu = p * win + (1 - p) * loss
    sigma = math.sqrt(p * (1 - p)) * (win - loss)
    return mu, sigma


def log_growth_stats(p: float, price: float, f: float) -> tuple[float, float]:
    """(mean, std) of per-trade log bankroll growth staking fraction `f`."""
    b = (1 - price) / price
    win, loss = math.log1p(f * b), math.log1p(-f)
    mu = p * win + (1 - p) * loss
    sigma = math.sqrt(p * (1 - p)) * (win - loss)
    return mu, sigma


# ---------------------------------------------------------------- Monte Carlo


def mc_bankroll(
    wins: int,
    losses: int,
    price: float,
    *,
    stake: float | None = None,
    fraction: float | None = None,
    n_trades: int = 500,
    n_paths: int = 4000,
    bankroll0: float = BANKROLL0,
    seed: int = SEED,
) -> dict:
    """Seeded bankroll simulation with parameter uncertainty.

    Each path first draws the TRUE win rate from Beta(wins+1, losses+1) —
    so small samples honestly widen the outcome distribution — then plays
    `n_trades` Bernoulli trades at `price` with either a flat dollar `stake`
    or a bankroll `fraction`. Barriers: double (2x) and halve (0.5x) of the
    starting bankroll; a path stops early when it can no longer cover its
    stake.
    """
    if (stake is None) == (fraction is None):
        raise ValueError("exactly one of stake/fraction")
    rng = random.Random(seed)
    b = (1 - price) / price
    terminals, maxdds = [], []
    doubled = halved = profitable = 0
    for _ in range(n_paths):
        p_true = rng.betavariate(wins + 1, losses + 1)
        bank, peak, dd = bankroll0, bankroll0, 0.0
        hit = None
        for _ in range(n_trades):
            s = stake if stake is not None else fraction * bank
            if bank < s or s <= 0:
                break
            bank += s * b if rng.random() < p_true else -s
            peak = max(peak, bank)
            dd = max(dd, (peak - bank) / peak)
            if hit is None:
                if bank >= 2 * bankroll0:
                    hit = "double"
                elif bank <= 0.5 * bankroll0:
                    hit = "halve"
        terminals.append(bank)
        maxdds.append(dd)
        doubled += hit == "double"
        halved += hit == "halve"
        profitable += bank > bankroll0
    terminals.sort()

    def q(frac: float) -> float:
        return terminals[min(len(terminals) - 1, int(frac * len(terminals)))]

    return {
        "terminal_p5": round(q(0.05), 2),
        "terminal_median": round(q(0.5), 2),
        "terminal_p95": round(q(0.95), 2),
        "p_profitable": round(profitable / n_paths, 3),
        "p_double_first": round(doubled / n_paths, 3),
        "p_halve_first": round(halved / n_paths, 3),
        "mean_max_drawdown": round(sum(maxdds) / n_paths, 3),
    }


# ----------------------------------------------------------------- evaluation


def entry_price(hyp: str, mean_fv: float) -> float:
    """The price discipline the engine enforces: margin below fair, never
    above the frozen ceiling, never a degenerate quote."""
    return min(entry_ceiling(hyp), max(0.02, mean_fv - MARGIN))


def evaluate_cell(cell: str, rows: list[dict], n_trades: int, n_paths: int) -> dict:
    hyp = rows[0]["hypothesis_id"]
    pairs = [(r["fair_value"], 1.0 if r["won"] else 0.0) for r in rows]
    w = sum(1 for _, y in pairs if y)
    n = len(pairs)
    p_hat = w / n
    lo, hi = wilson(w, n)
    mean_fv = sum(p for p, _ in pairs) / n
    ceiling = entry_ceiling(hyp)
    price = entry_price(hyp, mean_fv)
    mu_flat, sigma_flat = flat_stake_stats(p_hat, price, 1.0)
    kelly = kelly_fraction(p_hat, price)
    qk = kelly / 4
    out = {
        "cell": cell,
        "n_resolved": n,
        "series": len({r["series_id"] for r in rows}),
        "win_rate": round(p_hat, 4),
        "win_ci": [round(lo, 4), round(hi, 4)],
        "mean_fair_value": round(mean_fv, 4),
        "brier": round(brier(pairs), 4),
        "brier_skill_vs_base_rate": (
            None if (s := brier_skill(pairs)) is None else round(s, 4)
        ),
        "ece_5bin": round(ece(pairs), 4),
        "entry_ceiling": ceiling,
        "entry_price_assumed": round(price, 4),
        "ev_per_contract": round(ev_per_contract(p_hat, price), 4),
        "ev_per_contract_wilson_lo": round(ev_per_contract(lo, price), 4),
        "ev_per_contract_at_ceiling": round(ev_per_contract(p_hat, ceiling), 4),
        "kelly_full": round(kelly, 4),
        "kelly_quarter": round(qk, 4),
        "brownian_flat_1usd": {
            "mu_per_trade_usd": round(mu_flat, 4),
            "sigma_per_trade_usd": round(sigma_flat, 4),
            "p_double_before_halve": round(
                barrier_up_probability(mu_flat, sigma_flat, BANKROLL0, BANKROLL0 / 2), 3
            ),
            "expected_trades_to_barrier": (
                None
                if (t := expected_trades_to_barrier(mu_flat, sigma_flat, BANKROLL0, BANKROLL0 / 2))
                is None
                else round(t)
            ),
        },
        "mc_flat_1usd": mc_bankroll(
            w, n - w, price, stake=1.0, n_trades=n_trades, n_paths=n_paths
        ),
    }
    if qk > 0:
        mu_log, sigma_log = log_growth_stats(p_hat, price, qk)
        out["brownian_quarter_kelly"] = {
            "log_growth_per_trade": round(mu_log, 5),
            "p_double_before_halve": round(
                barrier_up_probability(mu_log, sigma_log, math.log(2), math.log(2)), 3
            ),
        }
        out["mc_quarter_kelly"] = mc_bankroll(
            w, n - w, price, fraction=qk, n_trades=n_trades, n_paths=n_paths
        )
    return out


def evaluate(n_trades: int = 500, n_paths: int = 4000) -> dict:
    instances = verdict.load_instances(replay=False, include_test=False)
    oracles = verdict.build_oracles({i["series_id"] for i in instances})
    rows = verdict.resolve_instances(instances, oracles)
    resolved = [r for r in rows if r["resolved"] and r.get("fair_value") is not None]
    cells: dict[str, list[dict]] = defaultdict(list)
    for r in resolved:
        cells[verdict._cell(r)].append(r)
    cell_reports = [
        evaluate_cell(cell, cell_rows, n_trades, n_paths)
        for cell, cell_rows in sorted(cells.items())
        if len(cell_rows) >= 10
    ]
    pooled = [(r["fair_value"], 1.0 if r["won"] else 0.0) for r in resolved]
    return {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "instances_total": len(instances),
        "instances_resolved": len(resolved),
        "pooled": {
            "n": len(pooled),
            "brier": round(brier(pooled), 4) if pooled else None,
            "brier_skill_vs_base_rate": (
                None if not pooled or (s := brier_skill(pooled)) is None else round(s, 4)
            ),
            "ece_10bin": round(ece(pooled, bins=10), 4) if pooled else None,
        },
        "cells": cell_reports,
        "notes": [
            ("Entry prices are ASSUMED (fair-0.04 capped at the frozen ceiling); zero live "
            "book evidence backs fill probability at those prices yet — quoted=0 on the "
            "captured intl books and PMUS book attach is still being wired. Treat EV as "
            "the calibration-implied edge IF fills happen at the disciplined price."),
            ("MC paths draw the true win rate from Beta(wins+1, losses+1): parameter "
            "uncertainty from small cells honestly widens the outcome distribution."),
            ("The registry bars (docs/experiments/registry.jsonl) remain the only "
            "PASS/FAIL rule; this harness is the H-MAPENTRY1 reporting layer."),
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--trades", type=int, default=500)
    ap.add_argument("--paths", type=int, default=4000)
    args = ap.parse_args()

    report = evaluate(args.trades, args.paths)
    out_dir = config.DATA_DIR / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(UTC).strftime("%Y%m%d")
    path = out_dir / f"quant_eval-{day}.json"
    path.write_text(json.dumps(report, indent=1))

    if args.json:
        print(json.dumps(report, indent=1))
        return
    p = report["pooled"]
    print(
        f"quant_eval — {report['instances_resolved']}/{report['instances_total']} resolved | "
        f"pooled Brier {p['brier']} (skill {p['brier_skill_vs_base_rate']}) ECE {p['ece_10bin']}"
    )
    print(
        f"{'cell':30} {'n':>4} {'win':>6} {'fv':>6} {'brier':>6} {'ece':>6} "
        f"{'entry':>6} {'EV/ct':>7} {'EVlo':>7} {'kelly':>6} {'P(2x)':>6} {'medT':>8}"
    )
    for c in report["cells"]:
        print(
            f"{c['cell']:30} {c['n_resolved']:>4} {c['win_rate']:>6.3f} "
            f"{c['mean_fair_value']:>6.3f} {c['brier']:>6.3f} {c['ece_5bin']:>6.3f} "
            f"{c['entry_price_assumed']:>6.3f} {c['ev_per_contract']:>+7.3f} "
            f"{c['ev_per_contract_wilson_lo']:>+7.3f} {c['kelly_full']:>6.3f} "
            f"{c['brownian_flat_1usd']['p_double_before_halve']:>6.3f} "
            f"{c['mc_flat_1usd']['terminal_median']:>8.2f}"
        )
    print(f"written: {path}")
    for note in report["notes"]:
        print(f"  ! {note}")


if __name__ == "__main__":
    main()
