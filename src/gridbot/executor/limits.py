"""Hard caps and the live-arming gate (fail-closed).

The gate READS state; it never changes it. dry_run is the default and the
only self-selectable mode — going live is the user's explicit act:
GRIDBOT_LIVE=1 in the environment, keys in the secrets file, compliance record
on disk. Any missing piece → dry_run with the reasons listed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from gridbot import config

COMPLIANCE_FILE = Path(__file__).resolve().parents[3] / "docs" / "COMPLIANCE.md"
# M1: an explicit affirmation line, not a topic word — a template or a
# "TODO: eligibility" stub must NOT satisfy the gate.
COMPLIANCE_MARKER = "eligibility confirmed: yes"

# Fail-small caps. Env-overridable within clamps; never unbounded.
MAX_STAKE_USD_CEILING = 100.0
DEFAULT_STAKE_USD = 25.0
DEFAULT_MAX_OPEN = 3
DEFAULT_MAX_DAILY_LOSS_USD = 50.0


def max_stake_usd() -> float:
    """Per-order stake cap in USD, clamped to the hard ceiling."""
    try:
        v = float(os.environ.get("GRIDBOT_MAX_STAKE_USD", DEFAULT_STAKE_USD))
    except ValueError:
        return DEFAULT_STAKE_USD
    return max(1.0, min(v, MAX_STAKE_USD_CEILING))


def max_open_positions() -> int:
    try:
        v = int(os.environ.get("GRIDBOT_MAX_OPEN", DEFAULT_MAX_OPEN))
    except ValueError:
        return DEFAULT_MAX_OPEN
    return max(1, min(v, 10))


def max_daily_loss_usd() -> float:
    try:
        v = float(os.environ.get("GRIDBOT_MAX_DAILY_LOSS", DEFAULT_MAX_DAILY_LOSS_USD))
    except ValueError:
        return DEFAULT_MAX_DAILY_LOSS_USD
    return max(1.0, min(v, 500.0))


@dataclass
class ArmingState:
    """Result of the live-arming gate. dry_run=True unless EVERY gate passes."""

    dry_run: bool = True
    reasons: list[str] = field(default_factory=list)

    @property
    def live(self) -> bool:
        return not self.dry_run


def check_armed(env: dict[str, str] | None = None) -> ArmingState:
    """Evaluate the arming gate. Read-only; fail-closed on any doubt."""
    e = os.environ if env is None else env
    reasons: list[str] = []

    if e.get("GRIDBOT_LIVE") != "1":
        reasons.append("GRIDBOT_LIVE!=1 (going live is the user's own act)")
    if not config._secret("POLYMARKET_US_KEY_ID"):
        reasons.append("POLYMARKET_US_KEY_ID not available (secrets file)")
    if not config._secret("POLYMARKET_US_SECRET_KEY"):
        reasons.append("POLYMARKET_US_SECRET_KEY not available (secrets file)")
    try:
        text = COMPLIANCE_FILE.read_text().lower()
        if COMPLIANCE_MARKER not in text:
            reasons.append("compliance record lacks eligibility confirmation")
    except OSError:
        reasons.append("docs/COMPLIANCE.md missing")

    return ArmingState(dry_run=bool(reasons), reasons=reasons)
