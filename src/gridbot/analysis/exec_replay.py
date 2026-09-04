"""EXEC1 offline maker-replay runner: orders + observations -> fill bracket -> accounting.

H-MAPENTRY1-EXEC1's runner. Given the held-out order population (already assembled by
:mod:`gridbot.analysis.exec_population`) and the per-order observations, apply the two
fill-bracket bounds from :mod:`gridbot.analysis.exec_fills` and roll the results up into
a per-bound accounting summary (means, series-clustered bootstrap CIs), an optional
calibration against real venue outcomes, and a markdown render.

Offline, deterministic, read-only: no network, no orders, no keys. Pure slow-loop
computation (iron rail 1) -- it never touches the engine, the arming state, or the gate.
The gate thresholds are deliberately NOT here (they are pending user confirmation); this
module produces the numbers, the user applies the bars.

Regime: an order's observations are "sized" iff ANY in-window observation carries a
non-None ``opp_size`` (the pmus BBO tape after the size extension). Sized orders bound with
a strict-touch + persistence rule with the queue policy per regime (behind the
displayed size when sized, one contract when not -- finding #8); the upper edge is
``UPPER_CONSUMPTION``. Both edges carry EXEC1-AMEND1's level-consumption channel and
pass ``order.top_size`` as ``queue_ahead``.

The per-fill PnL is ``y - cost`` in fractional-per-contract terms, where ``y`` is
``exec_population.y_side`` (1.0/0.0/None, never inverted). Unresolved maps (``y`` None) are
excluded from PnL/CI but are REPORTED (``n_unresolved_fills``) and reflected in the
half-resolved sensitivity.
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from gridbot.analysis.exec_fills import (
    UPPER_CONSUMPTION,
    UPPER_CONSUMPTION_MARKET,
    Fill,
    FillRule,
    simulate,
)
from gridbot.analysis.exec_population import Order, observations, y_side

# Import bootstrap_ci exactly as scripts/venue_pricing_cut.py does: insert the scripts dir
# on sys.path, then import the timing_chain symbol.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from timing_chain import bootstrap_ci

BOUNDS = ("lower", "upper")


@dataclass(frozen=True)
class Replayed:
    """One order's bracket outcome: the lower/upper fills plus regime/outcome."""

    order: Order
    lower: Fill
    upper: Fill
    regime: str  # "sized" if ANY in-window obs has opp_size not None, else "unsized"
    y: float | None  # y_side(order, winners): 1.0/0.0/None (never inverted)
    n_obs: int


def _bound_fill(r: Replayed, bound: str) -> Fill:
    return r.lower if bound == "lower" else r.upper


def replay_orders(orders, postquote_by_src, tape_by_slug, winners, clip_qty: float = 25.0,
                  real_orders: bool = False):
    """Replay every order through the two fill bounds, one :class:`Replayed` each.

    ``real_orders=True`` (the calibration fixture: orders that actually rested) lets
    BOTH edges use the level-consumption channel referenced to our cost. Hypothetical
    (dry-run) orders — AMEND1-NOTE1 — keep the lower edge touch-only and give the
    upper edge the market-referenced consumption signal.
    """
    out: list[Replayed] = []
    for o in orders:
        obs = observations(o, postquote_by_src, tape_by_slug)
        regime = "sized" if any(ob.opp_size is not None for ob in obs) else "unsized"
        lower_rule = FillRule(touch="strict", persist=True,
                              queue="behind_displayed" if regime == "sized" else "unsized",
                              consumption=real_orders)
        # Registry EXEC1: "exactly 1 contract when unsized" — for BOTH edges (review
        # finding #4: a 25:1 clip-vs-one-contract blend is not an expected PnL).
        qty = clip_qty if regime == "sized" else 1.0
        lower = simulate(
            lower_rule, o.cost, qty, o.t0_ns, obs, queue_ahead=o.top_size
        )
        upper_rule = UPPER_CONSUMPTION if real_orders else UPPER_CONSUMPTION_MARKET
        upper = simulate(upper_rule, o.cost, qty, o.t0_ns, obs,
                         queue_ahead=o.top_size)
        y = y_side(o, winners)
        out.append(Replayed(order=o, lower=lower, upper=upper, regime=regime,
                            y=y, n_obs=len(obs)))
    return out


def pnl_rows(replayed, bound: str) -> list[tuple[str, float, float]]:
    """Resolved PnL rows: (series_id, pnl_per_contract, qty) for a filled+resolved bound.

    Only fills where ``y`` is not None count; unresolved fills are EXCLUDED here but are
    counted in :func:`summarize` as ``n_unresolved_fills``.
    """
    rows = []
    for r in replayed:
        f = _bound_fill(r, bound)
        if f.filled and r.y is not None:
            rows.append((r.order.series_id, r.y - r.order.cost, f.qty))
    return rows


def _weighted_mean(pairs: list[tuple[float, float]]) -> float | None:
    """Quantity-weighted mean of (pnl, qty) pairs; None when no weight accumulates."""
    num = sum(p * q for p, q in pairs)
    den = sum(q for _, q in pairs)
    if den == 0.0:
        return None
    return num / den


def _one_bound_summary(r_list, bound: str, bootstrap) -> dict:
    resolved = [(r.order.series_id, r.y - r.order.cost, _bound_fill(r, bound).qty)
                for r in r_list
                if _bound_fill(r, bound).filled and r.y is not None]
    unresolved = [r for r in r_list if _bound_fill(r, bound).filled and r.y is None]
    contracts = sum(q for _, _, q in resolved)
    # series-clustered bootstrap over (series_id, pnl) pairs -- ONE per fill, NOT weighted.
    boot = bootstrap([(sid, pnl) for sid, pnl, _ in resolved]) if resolved else None
    # quantity-weighted mean over resolved fills (the accounting figure).
    mean_pnl = _weighted_mean([(pnl, q) for _, pnl, q in resolved])
    # sensitivity: fold unresolved fills in at pnl = 0.5 - cost (the fiction they settle even).
    half_pairs = [(pnl, q) for _, pnl, q in resolved] + [
        (0.5 - r.order.cost, _bound_fill(r, bound).qty) for r in unresolved
    ]
    sensitivity = _weighted_mean(half_pairs) if half_pairs else None
    # Review finding #5: leave-one-series-out means and the loss concentration, so
    # "the interval is below zero" can be checked against 2-3 dominant clusters.
    by_series: dict[str, list[float]] = {}
    for sid, pnl, _q in resolved:
        by_series.setdefault(sid, []).append(pnl)
    all_pnl = [pnl for _, pnl, _ in resolved]
    loo = []
    for sid in by_series:
        rest = [pnl for s2, pnl, _ in resolved if s2 != sid]
        if rest:
            loo.append(sum(rest) / len(rest))
    losses = sorted(sum(v) for v in by_series.values())  # most negative first
    total = sum(all_pnl)
    top3_share = (sum(losses[:3]) / total) if total < 0 and losses else None
    jackknife = {
        "loo_mean_min": min(loo) if loo else None,
        "loo_mean_max": max(loo) if loo else None,
        "loo_all_negative": bool(loo) and all(m < 0 for m in loo),
        "top3_series_loss_share": None if top3_share is None else round(top3_share, 3),
        "series_with_net_loss": sum(1 for v in by_series.values() if sum(v) < 0),
        "series_with_fills": len(by_series),
    }
    return {
        "unweighted_mean": (sum(all_pnl) / len(all_pnl)) if all_pnl else None,
        "jackknife": jackknife,
        "n_orders": len(r_list),
        "n_filled": sum(1 for r in r_list if _bound_fill(r, bound).filled),
        "n_filled_resolved": len(resolved),
        "n_unresolved_fills": len(unresolved),
        "contracts": contracts,
        "mean_pnl": mean_pnl,
        "point": boot["point"] if boot else None,
        "ci95": boot["ci95"] if boot else None,
        "n": boot["n"] if boot else 0,
        "n_series": boot["n_series"] if boot else 0,
        "sensitivity_unresolved_at_half": sensitivity,
    }


def summarize(replayed, bootstrap=bootstrap_ci) -> dict:
    """Full accounting summary, per bound, plus the top-level regime/reason/invariant block."""
    bounds = {b: _one_bound_summary(replayed, b, bootstrap) for b in BOUNDS}
    regime_mix = {
        "sized": sum(1 for r in replayed if r.regime == "sized"),
        "unsized": sum(1 for r in replayed if r.regime == "unsized"),
    }
    reasons = {
        b: Counter(_bound_fill(r, b).reason for r in replayed) for b in BOUNDS
    }
    # invariant: a lower fill must be an upper fill too, and lower never credits more.
    invariant_ok = all(
        (not r.lower.filled) or r.upper.filled
        for r in replayed
    ) and all(
        r.lower.qty <= r.upper.qty for r in replayed
    )
    return {
        "bound": bounds,
        "regime_mix": regime_mix,
        "reasons": {b: dict(c) for b, c in reasons.items()},
        "invariant_ok": invariant_ok,
        "n_orders": len(replayed),
    }


# --- calibration ---------------------------------------------------------------


def order_key(order: Order) -> tuple[str, int, str, int]:
    """The calibration join key: (series_id, map_number, us_side, t0_ns).

    Review 2026-09-04: the armed nights re-entered the same (series, map, side)
    after a cancel, so 48 real orders collapse to 38 three-part keys; t0_ns (the
    intent time) keeps every order distinct.
    """
    return (order.series_id, order.map_number, order.us_side, order.t0_ns)


def _calibrate_bound(r_list, bound: str, actual) -> dict:
    """Per-bound confusion matrix, precision/recall, fill-time MAE, recall by side."""
    tp = fp = fn = tn = 0
    time_abs = []
    sides = {"one_sided": [0, 0], "two_sided": [0, 0]}  # bucket -> [tp, tp+fn]
    for r in r_list:
        a = actual.get(order_key(r.order))
        if a is None or not a["rested"]:
            continue  # rejected/unrested orders are subtracted BEFORE the bracket
        pred = _bound_fill(r, bound).filled
        truth = a["filled"]
        if pred and truth:
            tp += 1
        elif pred and not truth:
            fp += 1
        elif not pred and truth:
            fn += 1
        else:
            tn += 1
        if truth:
            bucket = "one_sided" if a["one_sided"] else "two_sided"
            sides[bucket][1] += 1
            if pred:
                sides[bucket][0] += 1
        if truth and a.get("t_fill_ns") is not None \
                and _bound_fill(r, bound).t_fill_ns is not None:
            time_abs.append(abs(a["t_fill_ns"] - _bound_fill(r, bound).t_fill_ns) / 1e9)

    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    fill_time_mae_s = (sum(time_abs) / len(time_abs)) if time_abs else None
    # EXEC1-AMEND1 coverage rule: recall over orders the channel could see —
    # >= 1 in-window observation and never "undetectable" (someone above us).
    unobserved = fills_unobserved = undetectable = fills_undetectable = 0
    for r in r_list:
        a = actual.get(order_key(r.order))
        if a is None or not a["rested"]:
            continue
        f = _bound_fill(r, bound)
        if r.n_obs == 0:
            unobserved += 1
            fills_unobserved += int(bool(a["filled"]))
        elif f.reason == "undetectable":
            undetectable += 1
            fills_undetectable += int(bool(a["filled"]))
    seen_fills = (tp + fn) - fills_unobserved - fills_undetectable
    recall_detectable = tp / seen_fills if seen_fills > 0 else None
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision, "recall": recall,
        "n_unobserved": unobserved, "fills_unobserved": fills_unobserved,
        "n_undetectable": undetectable, "fills_undetectable": fills_undetectable,
        "recall_detectable": recall_detectable,
        "fill_time_mae_s": fill_time_mae_s,
        "recall_by_one_sidedness": {
            k: (s[0] / s[1]) if s[1] else None for k, s in sides.items()
        },
    }


def calibration(replayed, actual: dict) -> dict:
    """Confusion/recalibration of the predicted fills vs real venue outcomes.

    ``actual`` maps ``order_key(order)`` = (series_id, map_number, us_side, t0_ns)
    -> dict of {rested, filled, t_fill_ns, one_sided}. Only orders present in ``actual`` with
    ``rested`` True are scored (rejected/unrested orders were subtracted before the
    bracket -- exogenous). Per bound: tp/fp/fn/tn, precision and recall (None when
    undefined), ``fill_time_mae_s`` over orders with both an actual and predicted fill
    time, and recall split by book one-sidedness. Top-level ``n_rested`` /
    ``n_actual_fills`` ride with every result. The gate evaluation is NOT here.
    """
    rested = [order_key(r.order) for r in replayed
              if order_key(r.order) in actual and actual[order_key(r.order)]["rested"]]
    return {
        "lower": _calibrate_bound(replayed, "lower", actual),
        "upper": _calibrate_bound(replayed, "upper", actual),
        "n_rested": len(rested),
        "n_actual_fills": sum(1 for k in rested if actual[k]["filled"]),
    }


# --- render --------------------------------------------------------------------


def _fmt_ci(b, summary) -> str:
    ci = summary.get("ci95")
    point = summary.get("point")
    if ci is None or point is None:
        return f"no CI (n_series={summary['n_series']})"
    return f"{point} [{ci[0]:.4f}, {ci[1]:.4f}] (n_series={summary['n_series']})"


def _jack_line(b: dict) -> str:
    j = b.get("jackknife") or {}
    if not j or j.get("loo_mean_min") is None:
        return "- jackknife: n/a"
    return (f"- jackknife (leave one series out): mean in [{j['loo_mean_min']:+.4f}, "
            f"{j['loo_mean_max']:+.4f}], all negative={j['loo_all_negative']}; "
            f"top-3 series carry {j['top3_series_loss_share']} of the net loss; "
            f"{j['series_with_net_loss']}/{j['series_with_fills']} series net negative")


def _bound_section(label: str, summary: dict) -> str:
    lines = [
        f"#### {label} bound",
        f"- n_orders={summary['n_orders']}",
        (
            f"- fills: {summary['n_filled']} filled, {summary['n_filled_resolved']} "
            f"resolved, {summary['n_unresolved_fills']} unresolved"
        ),
        f"- contracts={summary['contracts']:.1f} (n_filled_resolved={summary['n_filled_resolved']})",
        f"- mean pnl={_none(summary['mean_pnl'])}/contract (n_filled_resolved={summary['n_filled_resolved']})",
        f"- bootstrapped mean={_fmt_ci('mean', summary)}",
        _jack_line(summary),
        f"- sensitivity at unresolved=half: {_none(summary['sensitivity_unresolved_at_half'])}",
    ]
    return "\n".join(lines)


def _none(v) -> str:
    return "None" if v is None else f"{v:.4f}"


def render(summary: dict, label: str) -> str:
    """Markdown render of the summary. Every number carries its n; never declares PASS/FAIL."""
    region = summary.get("bound", summary)
    mix = summary.get("regime_mix", None)
    reasons = summary.get("reasons", {})
    lines = [f"## {label}", ""]
    if mix is not None:
        lines.append(f"regime mix: sized={mix['sized']}, unsized={mix['unsized']} "
                     f"(n_orders={summary.get('n_orders')})")
        lines.append("")
    for b in BOUNDS:
        bs = region.get(b)
        if bs is not None:
            lines.append(_bound_section(b, bs))
            if b in reasons:
                lines.append(f"- reasons: {_reasons_str(reasons[b])}")
            lines.append("")
    if "invariant_ok" in summary:
        lines.append(f"bracket invariant holds: {summary['invariant_ok']}")
    return "\n".join(lines).rstrip() + "\n"


def _reasons_str(reasons: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in reasons.items())