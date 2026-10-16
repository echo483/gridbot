"""The scorecard result contract: no number prints without its interval.

Every measurement this package returns is a :class:`Score` — a value, a 95%
interval, the sample size, and **the number of independent series** the sample
came from.

``n_series`` is not decoration. gridbot's retracted "+0.198 skill" headline
happened because a row-pooled interval treated 11,016 correlated rows drawn
from ~80 series as if they were 11,016 independent observations. The interval
came out narrow and excluded zero; the same claim re-cut with the series as the
resampling unit ran the other way. So a score that cannot state its ``n_series``
is a score that cannot be trusted, and the constructor refuses to build one.

Science note: rows within a CS2 series share a map, a roster, and a market
book. They are not independent draws. Any interval downstream of this package
must resample SERIES.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["rate", "mean", "error", "usd", "fraction"]

#: Which way a value's interval should be read when it straddles zero.
EXCLUDES_ZERO = "excludes_zero"
INCLUDES_ZERO = "includes_zero"
UNDEFINED = "undefined"


@dataclass(frozen=True)
class Score:
    """A measurement, its uncertainty, and the sample it came from.

    Attributes:
        name: Human label, e.g. ``"band_gap"``.
        value: The point estimate, or ``None`` when undefined.
        ci: ``(lo, hi)`` 95% interval, or ``None`` when undefined.
        n: Number of observations (rows) behind ``value``.
        n_series: Number of independent series behind ``value``. Required.
        kind: How to format ``value`` — a rate, a mean, an error, dollars.
        note: Optional caveat rendered next to the number.
    """

    name: str
    value: float | None
    ci: tuple[float, float] | None
    n: int
    n_series: int
    kind: Kind = "mean"
    note: str | None = None
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.n < 0 or self.n_series < 0:
            raise ValueError("n and n_series must be non-negative")
        if self.n_series > self.n:
            raise ValueError(
                f"{self.name}: n_series={self.n_series} exceeds n={self.n} — "
                "a series must contain at least one observation"
            )
        if self.value is None and self.ci is not None:
            raise ValueError(f"{self.name}: undefined value cannot carry an interval")
        if self.value is not None and self.ci is not None:
            lo, hi = self.ci
            if lo > hi:
                raise ValueError(f"{self.name}: interval is inverted ({lo} > {hi})")

    @property
    def defined(self) -> bool:
        return self.value is not None

    @property
    def significant(self) -> bool:
        """True only when a real interval exists and excludes zero.

        This is deliberately strict: an undefined or absent interval reads
        ``False``, so nothing can be called significant by omission.
        """
        if self.ci is None or self.value is None:
            return False
        lo, hi = self.ci
        return lo > 0.0 or hi < 0.0

    @property
    def direction(self) -> str:
        if self.ci is None:
            return UNDEFINED
        return EXCLUDES_ZERO if self.significant else INCLUDES_ZERO

    def _fmt(self, x: float) -> str:
        if self.kind == "rate":
            return f"{x:.3f}"
        if self.kind == "usd":
            return f"{x:+.2f}"
        if self.kind in ("error", "fraction"):
            return f"{x:+.4f}"
        return f"{x:+.4f}"

    def render(self) -> str:
        """One line: value [lo, hi] (n=, series=). Never value alone."""
        if self.value is None:
            return f"{self.name}: undefined (n={self.n}, series={self.n_series})"
        body = self._fmt(self.value)
        if self.ci is not None:
            body += f" [{self._fmt(self.ci[0])}, {self._fmt(self.ci[1])}]"
        else:
            body += " [no interval]"
        out = f"{self.name}: {body} (n={self.n}, series={self.n_series})"
        if self.note:
            out += f"  ! {self.note}"
        return out

    __str__ = render


def series_count(groups: dict[str, list]) -> int:
    """Number of series keys in a ``{series_id: [...]}`` grouping."""
    return len(groups)
