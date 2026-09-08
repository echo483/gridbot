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
from gridbot.analysis.crossvenue_signal import (
    intl_moves,
    move_persistence,
    move_quality,
    summarise_moves,
    summarise_persistence,
)

THRESHOLDS = (2.0, 3.0, 5.0)
HORIZONS_S = (30, 60, 150, 300, 900)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", nargs="+", default=["20260907", "20260908"])
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--intl-jsonl", nargs="*", default=None)
    ap.add_argument("--persistence", action="store_true",
                    help="does a move HOLD or unwind? decides how to read NO_FOLLOW")
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

    if a.persistence:
        allp = []
        for _j, qs in series:
            allp += move_persistence(intl_moves(qs), qs, horizons_s=HORIZONS_S)
        s = summarise_persistence(allp, horizons_s=HORIZONS_S)
        print(f"\nPersistence of the {s['n']} events "
              f"(intl side only; decides how to read NO_FOLLOW):")
        print(f"{'horizon':>9}{'n':>6}{'median retrace':>16}"
              f"{'>=half reverted':>17}{'held (<=0)':>12}")
        for h in HORIZONS_S:
            b = s[f"h{h}s"]
            if not b["n"]:
                continue
            print(f"{h:>8}s{b['n']:>6}{b['median_retrace']:>16.3f}"
                  f"{b['frac_reverted_half']:>17.3f}{b['frac_held']:>12.3f}")
        print("\nretrace 0.0 = the move fully held; 1.0 = the mid returned to its start;")
        print("negative = the move continued. A move that unwinds was never a gap to take.")
        for name, lo, hi in (("moves < 5 ticks", 0.0, 5.0), ("moves >= 5 ticks", 5.0, 1e9)):
            grp = [p for p in allp if lo <= abs(p.move_ticks) < hi]
            b = summarise_persistence(grp, horizons_s=(150,))["h150s"]
            if b["n"]:
                print(f"  {name:<18} n={b['n']:>4}  median retrace @150s "
                      f"{b['median_retrace']:>6.3f}  >=half reverted "
                      f"{b['frac_reverted_half']:>5.3f}")

        # WHY they unwind: the mid cannot tell a repricing from one side of the
        # book flickering, but the two sides of the touch can.
        rows = []
        for _j, qs in series:
            mvs = intl_moves(qs)
            byt = {p.t_event_ns: p for p in move_persistence(mvs, qs, horizons_s=HORIZONS_S)}
            for mv in mvs:
                q, pp = move_quality(mv, qs), byt.get(mv.t_ns)
                if q is not None and pp is not None:
                    rows.append((q, pp))
        if rows:
            print(f"\nBy move QUALITY ({len(rows)} events with both reads):")
            print(f"{'quality':<15}{'n':>5}{'retrace@30s':>13}{'@150s':>9}"
                  f"{'@900s':>9}{'held@150s':>11}")
            for lab in ("repricing", "one_sided", "spread_change"):
                grp = [pp for q, pp in rows if q.label == lab]
                if not grp:
                    continue
                st = summarise_persistence(grp, horizons_s=HORIZONS_S)

                def cell(h: int, k: str, st: dict = st) -> str:
                    b = st[f"h{h}s"]
                    return f"{b[k]:.3f}" if b["n"] else "n/a"

                print(f"{lab:<15}{len(grp):>5}{cell(30, 'median_retrace'):>13}"
                      f"{cell(150, 'median_retrace'):>9}{cell(900, 'median_retrace'):>9}"
                      f"{cell(150, 'frac_held'):>11}")
            print("\nA two-sided repricing HOLDS; a one-sided move gives half of itself")
            "back. The mid alone cannot separate them."
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
