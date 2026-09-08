"""Is a PMUS book actually priced, or only present? Three axes, one verdict.

A basis, a spread edge and a fill model all divide by the assumption that the
book they read is a live market. When it is not, the arithmetic still runs and
the answer looks excellent -- which is precisely how H-MAPENTRY1's "+19pt
post-shock edge" turned out to be an artifact: never-repricing books won 95% of
the comparison while live books were negative. NOTE8 came out of that ("no
backtest number without a depth/fill model"), and this module is the missing
classifier that lets a population be cleaned BEFORE a number is quoted rather
than after it is believed.

THREE AXES, because any one of them alone passes a book that cannot be traded:

  two-sidedness  can a mid be formed at all? A one-sided or zeroed quote has no
                 midpoint, and 4 of 49 match books measured 2026-09-06/08 had
                 ZERO two-sided rows across their whole span.
  concentration  does it move MEANINGFULLY, or sit at one quote with occasional
                 blips? `aec-cs2-all-faze-2026-09-08` held the identical quote
                 (0.26, 0.27) for 185 of its 189 two-sided observations -- 95% --
                 while intl ranged 0.245 to 0.670. Nine transitions in and out of
                 degenerate states were enough to score 4.45 reprices/hour and
                 pass as live, and it then supplied 54% of the entire >=10-tick
                 tail of the cross-venue basis. Frequency passed it; concentration
                 does not.
  activity       does the quote ever move? 7 of those 49 never repriced once --
                 three of them held a two-sided quote perfectly still for ~3.5
                 hours across ~300 observations. A frozen book beats every live
                 counterparty in a backtest and pays nothing in reality.
  width          is the mid economically meaningful? This is the axis that is
                 easiest to skip and it is why `aec-cs2-oldm-pre-2026-09-07`
                 produced a 12.5-tick "basis": its median spread was 0.37 -- 37
                 ticks. A mid inside a 37-tick spread is a fiction, and nothing
                 can be crossed at it.

WHY PER-BOOK AND NOT POOLED. H-MAPENTRY1-NOTE9 reports match books at p50 spread
0.02, and that is true of the ROWS. Per BOOK the same tape carries median spreads
of 0.31, 0.37, 0.45, 0.58, 0.64, 0.90, 0.92 and 0.94. Pooling rows across books
hides the untradeable minority completely -- the same shape of error as summing a
per-event quantity from column medians.

CAUSALITY. ``classify`` reads whatever window it is handed and says nothing about
time. Handed a market's WHOLE history it is an analysis-time filter, appropriate
for excluding contaminated markets from a descriptive population. Handed a
TRAILING window ending at a decision instant it is causal and may gate an entry.
It is the caller's job to pass the right window; passing the full history and
then trading on the verdict is lookahead, and this docstring is the only warning
the type system can give.

FAILS CLOSED. Too little evidence is ``no_data``, never ``live``.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

# Defaults measured on data/pmus/books-2026090[678].jsonl, match books, 49
# (slug, day) books. They are DEFAULTS, not constants of nature: every one is a
# keyword argument, and `Liveness` carries the raw metrics so a caller can
# re-threshold without recomputing.
DEFAULT_MIN_OBS = 20                    # below this, span and rates are noise
DEFAULT_MIN_SPAN_S = 600.0              # 10 min; shorter cannot distinguish frozen from quiet
DEFAULT_MAX_ONE_SIDED_FRAC = 0.5        # more than half unquotable -> not a book
DEFAULT_MIN_REPRICES_PER_HOUR = 1.0     # observed live books: p25 4.6, p50 21.7
DEFAULT_MAX_MEDIAN_SPREAD = 0.05        # 5 ticks; match books that work sit at 0.01-0.03
# Share of two-sided observations sitting at the SINGLE most common quote. A
# book can clear the activity test on a handful of blips while spending almost
# all its life at one price -- reprice FREQUENCY passes it, reprice
# CONCENTRATION does not. Measured over 79 live-classified match books
# (2026-09-03..08): modal share p50 0.112, p90 0.277, and exactly ONE book above
# 0.80 (0.954). 0.50 is ~1.8x the healthy p90 and far below the offender, so it
# separates on the gap rather than being fitted to the one case.
DEFAULT_MAX_MODAL_SHARE = 0.50

# Severity order. A book can fail several axes at once; the verdict names the
# most fundamental failure, and `Liveness` carries the rest.
VERDICTS = ("no_data", "one_sided", "frozen", "pinned", "wide", "sluggish", "live")


@dataclass(frozen=True)
class Liveness:
    """Verdict plus every metric it was derived from."""

    verdict: str
    n_obs: int
    n_two_sided: int
    n_reprices: int
    span_s: float
    reprices_per_hour: float | None
    frac_one_sided: float
    median_spread: float | None
    modal_quote_share: float | None  # share of two-sided rows at the commonest quote

    @property
    def tradeable(self) -> bool:
        """Only an unambiguously live book. Everything else is excluded."""
        return self.verdict == "live"

    @property
    def usable_for_basis(self) -> bool:
        """A basis needs a meaningful mid; it does not need a busy one.

        ``sluggish`` is kept: a slow book still prices, and slowness is the very
        staleness the cross-venue thesis is about. ``frozen`` and ``wide`` are
        not kept -- one has no price discovery at all and the other has no
        meaningful midpoint.
        """
        return self.verdict in ("live", "sluggish")


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[len(s) // 2]


def classify(
    quotes: list[tuple[int, float | None, float | None]],
    *,
    min_obs: int = DEFAULT_MIN_OBS,
    min_span_s: float = DEFAULT_MIN_SPAN_S,
    max_one_sided_frac: float = DEFAULT_MAX_ONE_SIDED_FRAC,
    min_reprices_per_hour: float = DEFAULT_MIN_REPRICES_PER_HOUR,
    max_median_spread: float = DEFAULT_MAX_MEDIAN_SPREAD,
    max_modal_share: float = DEFAULT_MAX_MODAL_SHARE,
) -> Liveness:
    """Classify one book from its ``(t_ns, bid, ask)`` observations.

    Order need not be sorted for the counts, but reprices are counted in the
    order given, so hand it a time-ordered series.
    """
    n = len(quotes)
    two: list[tuple[float, float]] = []
    counts: dict[tuple[float, float], int] = {}
    spreads: list[float] = []
    reprices = 0
    last: tuple[float, float] | None = None
    t_lo = t_hi = None
    for t, b, a in quotes:
        t_lo = t if t_lo is None else min(t_lo, t)
        t_hi = t if t_hi is None else max(t_hi, t)
        if not (isinstance(b, (int, float)) and isinstance(a, (int, float))):
            continue
        if b <= 0 or a <= 0:
            continue  # a zero is this venue's dead-book placeholder (min tick 0.01)
        pair = (float(b), float(a))
        two.append(pair)
        counts[pair] = counts.get(pair, 0) + 1
        spreads.append(float(a) - float(b))
        if last is not None and last != (b, a):
            reprices += 1
        last = (float(b), float(a))

    span_s = (t_hi - t_lo) / 1e9 if (t_lo is not None and t_hi is not None) else 0.0
    frac_one = 1.0 - (len(two) / n) if n else 1.0
    rph = (reprices / (span_s / 3600.0)) if span_s >= min_span_s else None
    med_sp = _median(spreads)
    modal = (max(counts.values()) / len(two)) if two else None

    if n < min_obs or span_s < min_span_s:
        verdict = "no_data"
    elif frac_one > max_one_sided_frac:
        verdict = "one_sided"
    elif reprices == 0:
        verdict = "frozen"
    elif modal is not None and modal > max_modal_share:
        # Moves, but spends almost all its life at one quote.
        verdict = "pinned"
    elif med_sp is not None and med_sp > max_median_spread:
        verdict = "wide"
    elif rph is not None and rph < min_reprices_per_hour:
        verdict = "sluggish"
    else:
        verdict = "live"

    return Liveness(
        verdict=verdict,
        n_obs=n,
        n_two_sided=len(two),
        n_reprices=reprices,
        span_s=round(span_s, 1),
        reprices_per_hour=round(rph, 2) if rph is not None else None,
        frac_one_sided=round(frac_one, 4),
        median_spread=round(med_sp, 4) if med_sp is not None else None,
        modal_quote_share=round(modal, 4) if modal is not None else None,
    )


# --- trailing-window liveness -----------------------------------------------


DEFAULT_TRAILING_WINDOW_NS = 1_800_000_000_000   # 30 min
TRAILING_MIN_OBS = 10
TRAILING_MIN_SPAN_S = 300.0


class Trailing:
    """Was this book alive in the ``window`` ENDING at each instant?

    A book does not have to be dead for its whole life to poison a measurement.
    `aec-cs2-lgc-mgc-2026-09-08` tracked intl within 0.5-2 ticks for three hours,
    then its mid froze at 0.850 at 06:30 and stayed there for the last hour while
    intl swung 0.570 -> 0.170 -> 1.000 and the match resolved. Classified over the
    WHOLE day it is comfortably `live`; the frozen hour supplied its entire
    38.5-tick p90.

    `classify` already documents that it is causal when handed a trailing window.
    This is that usage made available, so a consumer applies liveness POINTWISE
    rather than once per book. Pooled effect on the cross-venue basis: p90 falls
    from 9.0 ticks to 3.5 while the median is unchanged at 1.5 — the long tail
    was books dying mid-match, not dislocation.

    Thresholds are looser than the global ones on purpose: a 30-minute slice
    holds far fewer observations than a day, so the global `min_obs`/`min_span_s`
    would report `no_data` for every point.
    """

    __slots__ = ("_cache", "_kw", "_rows", "_ts", "_window")

    def __init__(self, rows: list[tuple[int, float | None, float | None]],
                 window_ns: int = DEFAULT_TRAILING_WINDOW_NS, **kw) -> None:
        self._rows = sorted(rows)
        self._ts = [r[0] for r in self._rows]
        self._window = window_ns
        self._kw = {"min_obs": TRAILING_MIN_OBS,
                    "min_span_s": TRAILING_MIN_SPAN_S, **kw}
        self._cache: dict[int, Liveness] = {}

    def at(self, t_ns: int) -> Liveness:
        """Liveness over ``(t - window, t]``. Cached per instant."""
        hit = self._cache.get(t_ns)
        if hit is None:
            lo = bisect.bisect_left(self._ts, t_ns - self._window)
            hi = bisect.bisect_right(self._ts, t_ns)
            hit = classify(self._rows[lo:hi], **self._kw)
            self._cache[t_ns] = hit
        return hit

    def usable_at(self, t_ns: int) -> bool:
        return self.at(t_ns).usable_for_basis
