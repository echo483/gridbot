"""Central config: hosts, rate ceilings, data paths, and keyring-only secrets.

Secrets policy (same posture as cs2agent — see SECURITY.md):
- Secrets live in the OS keyring under service ``gridbot`` (SecretService/KWallet),
  written by ``scripts/setup_secrets.py`` via getpass. No secret is read from files.
- An env var of the same name overrides the keyring ONLY for tests/CI with fakes.
- Every secret read at runtime is registered with the log redactor.
"""

from __future__ import annotations

import os
from pathlib import Path

from gridbot.redaction import register_secret

KEYRING_SERVICE = "gridbot"

# GRID hosts — live-verified 2026-06-22 (grid-api skill): api-op.grid.gg ONLY.
# api.grid.gg authenticates but returns PERMISSION_DENIED for OA keys.
GRID_CENTRAL_DATA_URL = "https://api-op.grid.gg/central-data/graphql"
GRID_SERIES_STATE_URL = "https://api-op.grid.gg/live-data-feed/series-state/graphql"

# Polymarket public endpoints (no auth needed for market data).
PM_GAMMA_URL = "https://gamma-api.polymarket.com"
PM_CLOB_URL = "https://clob.polymarket.com"
PM_WS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# GRID Open Access rate limits — hard ceilings, do not exceed (grid-api skill).
GRID_CENTRAL_RPM = 20
GRID_SERIES_STATE_RPM_GLOBAL = 180
GRID_SERIES_STATE_RPM_PER_SERIES = 6

# Capture cadences (seconds). SS live cadence 12s keeps a margin inside 6/min.
DISCOVERY_CADENCE_S = 900          # Central Data allSeries sweep (GRID-recommended 15-30 min)
DISCOVERY_WINDOW = (-4 * 3600, 1 * 3600)  # -4h/+1h scheduled window (rolling starts)
SS_LIVE_POLL_S = 12
SS_IDLE_POLL_S = 60                # discovered but not yet started
PM_DISCOVERY_CADENCE_S = 300
PM_SNAPSHOT_CADENCE_S = 600

# GRID title ids. CS2 confirmed = 28; others are discovered by scripts/grid_probe.py
# and belong in TITLE_IDS once confirmed against the live API (never guessed).
TITLE_ID_CS2 = "28"
TITLE_IDS: dict[str, str] = {"cs2": TITLE_ID_CS2}
_extra = os.environ.get("GRIDBOT_EXTRA_TITLES", "")  # e.g. "lol=3,dota2=4" once probed
for pair in filter(None, _extra.split(",")):
    k, _, v = pair.partition("=")
    TITLE_IDS[k.strip()] = v.strip()

DATA_DIR = Path(os.environ.get("GRIDBOT_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))


class MissingSecret(RuntimeError):
    """Raised with a secret-FREE message when a required secret is absent."""


def _secret(name: str) -> str | None:
    env = os.environ.get(name)
    if env:
        register_secret(env)
        return env
    import keyring  # imported lazily so tests without a keyring backend still import config

    val = keyring.get_password(KEYRING_SERVICE, name)
    if val:
        register_secret(val)
    return val or None


def require(name: str) -> str:
    val = _secret(name)
    if not val:
        raise MissingSecret(
            f"secret {name!r} not found in keyring service {KEYRING_SERVICE!r} "
            f"(run scripts/setup_secrets.py) and no env override present"
        )
    return val


def grid_api_key() -> str:
    """GRID Open Access ``x-api-key``. Header-only — NEVER as a URL param."""
    return require("GRID_API_KEY")
