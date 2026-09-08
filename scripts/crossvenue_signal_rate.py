#!/usr/bin/env python3
"""How often does intl reprice? The trigger's rate, on the intl side alone.

The CROSSVENUE1 lane's clean unconditional basis measured 1.5 ticks against a
proposed 2-tick cost bar, so the lane now depends on a signal selecting the tail.
This script characterises the candidate trigger -- an intl mid move of at least
N ticks inside a trailing window -- WITHOUT looking at the basis it would select,
and without reading a PMUS book at all.

That separation is the point. With enough thresholds some cut always clears 2
ticks (Quantopian lecture 22, multiple comparisons). Fixing the trigger's
parameters from the intl side's own dynamics, and freezing them into the
pre-registration before the conditional measurement is possible, is what makes
the eventual measurement confirmatory rather than exploratory.

TRAINING SERIES ONLY (iron rail 2) -- the same holdout gate as the basis script,
which raises rather than filtering.

Run: uv run python scripts/crossvenue_signal_rate.py --intl-jsonl FILE [FILE ...]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from gridbot.analysis.crossvenue_basis import require_training_only, split_by_boundary
from gridbot.analysis.crossvenue_io import (
    load_intl_catalog,
    load_intl_quotes,
    load_mappings,
)
from gridbot.analysis.crossvenue_join import join_markets
from gridbot.analysis.crossvenue_signal import intl_moves, summarise_moves

THRESHOLDS = (2.0, 3.0, 5.0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", nargs="+", default=["20260907", "20260908"])
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--intl-jsonl", nargs="*", default=None)
    a = ap.parse_args(argv)
    data_dir = Path(a.data_dir)

    first_mapped, mappings = load_mappings(data_dir)
    joined, _ = join_markets(load_intl_catalog(a.days, data_dir), mappings)
    train_ids, _hold = split_by_boundary([j.grid_series_id for j in joined], first_mapped)
    train = [j for j in joined if j.grid_series_id in set(train_ids)]
    require_training_only([j.grid_series_id for j in train], first_mapped)

    intl = load_intl_quotes({j.intl.yes_token for j in train}, a.days, data_dir,
                            [Path(p) for p in a.intl_jsonl] if a.intl_jsonl else None)
    series = [(j, intl.get(j.intl.yes_token, [])) for j in train]
    series = [(j, q) for j, q in series if len(q) >= 2]
    print(f"{len(series)} training markets with an intl series "
          f"(intl side only; no PMUS book read, no basis computed)\n")

    for thr in THRESHOLDS:
        n = 0
        span_h = 0.0
        sizes: list[float] = []
        for _j, qs in series:
            mv = intl_moves(qs, min_ticks=thr)
            n += len(mv)
            span_h += (qs[-1].t_ns - qs[0].t_ns) / 3.6e12
            sizes += [abs(m.ticks) for m in mv]
        sizes.sort()
        rate = n / span_h if span_h else 0.0
        med = sizes[len(sizes) // 2] if sizes else 0.0
        p90 = sizes[int(0.9 * len(sizes))] if sizes else 0.0
        print(f"  >= {thr:>3.0f} ticks / 30s : {n:>5} events over {span_h:>7.1f} market-hours "
              f"= {rate:>5.2f}/h   median {med:>5.1f}t   p90 {p90:>5.1f}t")

    print(f"\n{'market':<34}{'intl evts':>10}{'hours':>7}{'moves>=2t':>11}{'per_h':>7}{'med t':>7}")
    for j, qs in sorted(series, key=lambda kv: -len(kv[1])):
        span = (qs[-1].t_ns - qs[0].t_ns) / 1e9
        s = summarise_moves(intl_moves(qs), span)
        print(f"{j.pmus_market_slug[:34]:<34}{len(qs):>10,}{span / 3600:>7.1f}"
              f"{s['n']:>11}{s['per_hour']:>7.2f}{s.get('median_ticks', 0):>7.1f}")
    print("\nRates vary by an order of magnitude: a ~4h span is an IN-PLAY window,")
    print("a ~24h span is listing-to-settlement and is mostly dormant. Pool with care.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
