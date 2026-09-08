#!/usr/bin/env python3
"""First look at the intl-vs-PMUS basis, on TRAINING series only.

H-CROSSVENUE1 is NOT pre-registered (iron rail 2), so this script may not touch
the holdout. It calls ``require_training_only``, which RAISES rather than
filtering when a holdout series is handed to it, and which fails closed on a
series whose first-mapped time is unknown. Nothing here is an edge claim, a
verdict, or a PnL: it reports the distribution of a price difference.

WHAT IT DOES. Joins the intl CS2 catalog to the PMUS mappings (crossvenue_join,
which settles orientation or refuses), keeps the training side of the
pre-registered boundary, then for each market builds

  intl  -- event-driven: every WS ``price_change`` carries best_bid/best_ask
  PMUS  -- sampled: our own polled BBO tape

and joins them AS-OF ON THE PMUS CLOCK (crossvenue_basis.basis_series): for each
PMUS observation, what intl believed at that instant, never after it.

BOOK LIVENESS IS APPLIED BEFORE POOLING. gridbot.analysis.book_liveness excludes
frozen, one-sided and pathologically wide PMUS books, because each of those
produces a large basis that is entirely an artifact -- the mechanism behind
H-MAPENTRY1's phantom "+19pt post-shock edge" (NOTE8). Both pooled figures are
printed so the size of the contamination is visible rather than asserted.

READ THE COVERAGE COLUMN FIRST. The binding constraint on this lane is not the
basis, it is that we mostly cannot see the venue we would trade -- see
docs/analysis/2026-09-08-crossvenue1-readiness.md. A market with thousands of
intl events and zero PMUS observations contributes nothing, and that is the
finding, not a bug in this script.

Intl books are ~12GB/day, so the intl side is read through a token filter. Pass
``--intl-jsonl`` with pre-filtered rows to skip the scan:

    grep -F -f tokens.txt data/pm/books-20260907.jsonl > intl.jsonl

Run: uv run python scripts/crossvenue_basis.py [--days 20260907 20260908]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from gridbot.analysis.book_liveness import classify as classify_book
from gridbot.analysis.crossvenue_basis import (
    basis_series,
    require_training_only,
    split_by_boundary,
    summarise,
)
from gridbot.analysis.crossvenue_io import (
    load_intl_catalog,
    load_intl_quotes,
    load_mappings,
    load_pmus_quotes,
)
from gridbot.analysis.crossvenue_join import join_markets


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", nargs="+", default=["20260907", "20260908"])
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--intl-jsonl", nargs="*", default=None,
                    help="pre-filtered intl rows, to skip the ~12GB/day scan")
    ap.add_argument("--max-intl-age-s", type=float, default=None,
                    help="drop points whose intl quote is staler than this")
    a = ap.parse_args(argv)
    data_dir = Path(a.data_dir)

    first_mapped, mappings = load_mappings(data_dir)
    joined, _refusals = join_markets(load_intl_catalog(a.days, data_dir), mappings)
    train_ids, holdout_ids = split_by_boundary([j.grid_series_id for j in joined], first_mapped)
    train = [j for j in joined if j.grid_series_id in set(train_ids)]
    # The gate, on the set we are about to read. Raises if anything holdout slipped in.
    require_training_only([j.grid_series_id for j in train], first_mapped)
    print(f"joined markets {len(joined)} -> TRAINING {len(train)} "
          f"(holdout {len(set(holdout_ids))} series NOT read)\n")

    intl = load_intl_quotes({j.intl.yes_token for j in train}, a.days, data_dir,
                            [Path(p) for p in a.intl_jsonl] if a.intl_jsonl else None)
    pmus = load_pmus_quotes({j.pmus_market_slug for j in train}, a.days, data_dir)
    max_age = int(a.max_intl_age_s * 1e9) if a.max_intl_age_s else None

    print(f"{'market':<32} {'intl evts':>9} {'pmus obs':>8} {'book':>9} {'n':>5} "
          f"{'|basis|t':>9} {'>=2t':>5} {'age s':>7}")
    pooled, clean, covered = [], [], 0
    for j in train:
        iq = intl.get(j.intl.yes_token, [])
        pq = pmus.get(j.pmus_market_slug, [])
        # NOTE8's artifact is excluded HERE, before any number is formed -- a
        # frozen or 37-tick-wide book produces a large, entirely fake basis.
        lv = classify_book([(q.t_ns, q.bid, q.ask) for q in pq])
        pts = basis_series(pq, iq, j.orientation, max_age)
        pooled += pts
        if lv.usable_for_basis:
            clean += pts
        s = summarise(pts)
        if s["n"]:
            covered += 1
            print(f"{j.pmus_market_slug[:32]:<32} {len(iq):>9,} {len(pq):>8,} "
                  f"{lv.verdict:>9} {s['n']:>5,} "
                  f"{s['median_abs_basis_ticks']:>9.2f} {s['frac_abs_ge_2_ticks']:>5.2f} "
                  f"{s['median_intl_age_s']:>7.1f}")
        else:
            print(f"{j.pmus_market_slug[:32]:<32} {len(iq):>9,} {len(pq):>8,} "
                  f"{lv.verdict:>9} {0:>5}   (no PMUS coverage)")
    n_intl = sum(1 for j in train if intl.get(j.intl.yes_token))
    n_pmus = sum(1 for j in train if pmus.get(j.pmus_market_slug))
    n_both = sum(1 for j in train
                 if intl.get(j.intl.yes_token) and pmus.get(j.pmus_market_slug))
    print(f"\ncoverage over {len(train)} training markets:")
    print(f"   intl series present            {n_intl:>4}   "
          f"(intl CS2 capture began 2026-09-07)")
    print(f"   PMUS book present              {n_pmus:>4}")
    print(f"   BOTH -- a basis is computable  {n_both:>4}")
    print(f"   with actual overlapping points {covered:>4}")
    print("\nPOOLED, ALL BOOKS (contaminated -- shown only so the difference is visible):")
    for k, v in summarise(pooled).items():
        print(f"   {k:<26} {v}")
    print("\nPOOLED, LIVE/SLUGGISH BOOKS ONLY (descriptive -- no edge claim, no verdict):")
    for k, v in summarise(clean).items():
        print(f"   {k:<26} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
