"""Sizing parameters: versioned config + env overrides, fail-closed validation.

Implements the H-SIZING0 spec (docs/superpowers/specs/2026-08-25-sizing-layer.md).
Sizing consumes the served model artifact only — no LLM ever touches this path.
All parameters are runtime-adjustable via env (GRIDBOT_SIZING_*) without code
changes; every override is echoed in ``.as_log_dict`` so stakes are exactly
reproducible from logs alone.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("data/sizing/sizing_config.json")


@dataclass(frozen=True)
class SizingParams:
    """Frozen value object; mutate only via :meth:`with_overrides`."""

    bankroll: float = 1_000.0          # quote currency; user-set at go-live
    kelly_divisor: float = 8.0         # 1/8-Kelly default
    min_edge: float = 0.05             # skip quotes within 5c of fair
    max_price: float = 0.90            # never pay above 90c regardless of q
    max_stake_frac: float = 0.02       # 2% of bankroll per entry
    max_open_per_series: int = 1       # no averaging into a losing state
    max_daily_loss_frac: float = 0.05  # circuit breaker
    wilson_margin: float = 0.02        # q_eff = min(q, ci_lo - margin)
    wilson_z: float = 1.96             # 95% lower bound

    def __post_init__(self) -> None:
        # fail-closed: any nonsense parameter refuses construction
        if self.bankroll <= 0:
            raise ValueError(f"bankroll must be > 0, got {self.bankroll}")
        if not (1.0 <= self.kelly_divisor <= 100.0):
            raise ValueError(f"kelly_divisor out of [1,100]: {self.kelly_divisor}")
        if not (0.0 < self.min_edge < 0.5):
            raise ValueError(f"min_edge out of (0,0.5): {self.min_edge}")
        if not (0.05 <= self.max_price <= 0.99):
            raise ValueError(f"max_price out of [0.05,0.99]: {self.max_price}")
        if not (0.0 < self.max_stake_frac <= 0.10):
            raise ValueError(f"max_stake_frac out of (0,0.10]: {self.max_stake_frac}")
        if self.max_open_per_series < 1:
            raise ValueError("max_open_per_series must be >= 1")
        if not (0.0 < self.max_daily_loss_frac <= 0.50):
            raise ValueError(
                f"max_daily_loss_frac out of (0,0.50]: {self.max_daily_loss_frac}"
            )
        if not (0.0 <= self.wilson_margin <= 0.10):
            raise ValueError(f"wilson_margin out of [0,0.10]: {self.wilson_margin}")
        if not (1.0 <= self.wilson_z <= 4.0):
            raise ValueError(f"wilson_z out of [1,4]: {self.wilson_z}")

    def with_overrides(self, env: dict[str, str] | None = None) -> "SizingParams":
        """Apply GRIDBOT_SIZING_<FIELD> env overrides (or an injected map)."""
        source = env if env is not None else dict(os.environ)
        updates: dict[str, object] = {}
        for f in fields(self):
            key = f"GRIDBOT_SIZING_{f.name.upper()}"
            if key in source:
                updates[f.name] = type(getattr(self, f.name))(source[key])
        if not updates:
            return self
        return SizingParams(**{**asdict(self), **updates})

    def as_log_dict(self) -> dict:
        return asdict(self)


def load_params(config_path: Path | None = None,
                env: dict[str, str] | None = None) -> SizingParams:
    """Load JSON config (if present) then env overrides. Fail-closed."""
    path = config_path or DEFAULT_CONFIG_PATH
    base: dict = {}
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: config must be a JSON object")
        base = raw
    return SizingParams(**base).with_overrides(env)
