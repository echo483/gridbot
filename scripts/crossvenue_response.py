#!/usr/bin/env python3
"""Does PMUS follow intl? The CROSSVENUE1 mechanism claim, on training data.

Everything else in the lane is plumbing for one assertion: intl and PMUS are
segmented, so PMUS goes STALE after information arrives and a maker taking intl's
side of that staleness has positive expectancy. This script measures it.

Each frozen intl repricing event (crossvenue_signal, >=2 ticks / 30s) is labelled
by what the PMUS book did around it:

  STALE      intl moved, PMUS had not, PMUS followed inside the horizon  -> target
  INFORMED   PMUS had ALREADY moved that way before the event fired      -> pick-off
  NO_FOLLOW  PMUS never moved that way inside the horizon                -> no convergence

INFORMED is the label that keeps this honest. A study asking only "does PMUS
eventually follow" scores a pick-off as a success; that pick-off is the -6.5pt
fill-selection cost (NOTE6) in observable form.

Books are filtered through book_liveness first: a frozen or 37-tick-wide book
manufactures both huge basis and fake staleness (NOTE8, NOTE13).

--horizon-sweep reports label mix against several horizons, including the
engine's own 150s maker order lifetime. A follow slower than the order TTL is not
tradeable by a resting maker whatever the tape says.

TRAINING SERIES ONLY (iron rail 2); the gate raises rather than filtering.
No edge claim: a followed gap is not a filled order.

Run: uv run python scripts/crossvenue_response.py [--intl-jsonl FILE ...] [--horizon-sweep]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from gridbot.analysis.book_liveness import classify
from gridbot.analysis.crossvenue_basis import require_training_only, split_by_boundary
from gridbot.analysis.crossvenue_io import (
    load_intl_catalog,
    load_intl_quotes,
    load_mappings,
    load_pmus_quotes,
)
from gridbot.analysis.crossvenue_join import join_markets
from gridbot.analysis.crossvenue_response import responses, summarise_responses
from gridbot.analysis.crossvenue_signal import intl_moves
from gridbot.analysis.trade_prints import DEFAULT_TTL_NS

SWEEP_S = (150, 300, 600, 1800, 3600)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", nargs="+", default=["20260907", "20260908"])
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--intl-jsonl", nargs="*", default=None)
    ap.add_argument("--horizon-sweep", action="store_true")
    a = ap.parse_args(argv)
    dd = Path(a.data_dir)

    first, maps = load_mappings(dd)
    joined, _ = join_markets(load_intl_catalog(a.days, dd), maps)
    train_ids, _h = split_by_boundary([j.grid_series_id for j in joined], first)
    train = [j for j in joined if j.grid_series_id in set(train_ids)]
    require_training_only([j.grid_series_id for j in train], first)

    intl = load_intl_quotes({j.intl.yes_token for j in train}, a.days, dd,
                            [Path(p) for p in a.intl_jsonl] if a.intl_jsonl else None)
    pmus = load_pmus_quotes({j.pmus_market_slug for j in train}, a.days, dd)

    print(f"{'market':<32}{'book':>9}{'moves':>7}{'resp':>6}{'stale':>7}{'inf':>5}"
          f"{'nofol':>7}{'|b|STALE':>10}{'lag s':>8}")
    usable = []
    for j in train:
        iq, pq = intl.get(j.intl.yes_token, []), pmus.get(j.pmus_market_slug, [])
        if not iq or not pq:
            continue
        lv = classify([(q.t_ns, q.bid, q.ask) for q in pq])
        rs = responses(intl_moves(iq), pq, j.orientation)
        if lv.usable_for_basis:
            usable.append(j)
        s = summarise_responses(rs)
        if not s["n"]:
            print(f"{j.pmus_market_slug[:32]:<32}{lv.verdict:>9}"
                  f"{len(intl_moves(iq)):>7}{0:>6}")
            continue
        print(f"{j.pmus_market_slug[:32]:<32}{lv.verdict:>9}{len(intl_moves(iq)):>7}"
              f"{s['n']:>6}{s['n_stale']:>7}{s['n_informed']:>5}{s['n_no_follow']:>7}"
              f"{s['median_abs_basis_ticks_STALE']!s:>10}"
              f"{s['median_follow_lag_s']!s:>8}")

    pooled = []
    for j in usable:
        pooled += responses(intl_moves(intl[j.intl.yes_token]),
                            pmus[j.pmus_market_slug], j.orientation)
    print(f"\nPOOLED over {len(usable)} usable book(s) "
          f"(training; descriptive -- no edge claim):")
    for k, v in summarise_responses(pooled).items():
        print(f"   {k:<32} {v}")

    if a.horizon_sweep:
        print(f"\nHorizon sweep (engine maker order lifetime is "
              f"{DEFAULT_TTL_NS / 1e9:.0f}s):")
        print(f"{'horizon':>9}{'n':>5}{'stale':>7}{'informed':>10}{'no_follow':>11}"
              f"{'|b|STALE':>10}{'lag p50':>9}")
        for h in SWEEP_S:
            rs = []
            for j in usable:
                rs += responses(intl_moves(intl[j.intl.yes_token]),
                                pmus[j.pmus_market_slug], j.orientation,
                                horizon_ns=h * 10**9)
            s = summarise_responses(rs)
            if not s["n"]:
                continue
            print(f"{h:>8}s{s['n']:>5}{s['n_stale']:>7}{s['n_informed']:>10}"
                  f"{s['n_no_follow']:>11}{s['median_abs_basis_ticks_STALE']!s:>10}"
                  f"{s['median_follow_lag_s']!s:>9}")
        print("\nA follow slower than the order TTL is not tradeable by a resting maker.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
