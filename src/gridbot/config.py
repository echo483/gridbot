"""Central config: hosts, rate ceilings, data paths, and keyring-only secrets.

Secrets policy (same posture as cs2agent — see SECURITY.md):
- Secrets live in the OS keyring under service ``gridbot`` (SecretService/KWallet),
  written by ``scripts/setup_secrets.py`` via getpass. No secret is read from files.
- An env var of the same name overrides the keyring ONLY for tests/CI with fakes.
- Every secret read at runtime is registered with the log redactor.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from gridbot.redaction import register_secret

KEYRING_SERVICE = "gridbot"

# GRID hosts — live-verified 2026-06-22 (grid-api skill): api-op.grid.gg ONLY.
# api.grid.gg authenticates but returns PERMISSION_DENIED for OA keys.
GRID_CENTRAL_DATA_URL = "https://api-op.grid.gg/central-data/graphql"
GRID_SERIES_STATE_URL = "https://api-op.grid.gg/live-data-feed/series-state/graphql"

# Supabase corpus (project cs2agent, shared with the historical record).
# URL is non-secret infra; the sb_secret write key lives in the keyring.
SUPABASE_URL = "https://pbwchzjwukxngeptmuju.supabase.co"

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

# GRID's permanent looping test fixtures (tournament "GRID-TEST", observed live
# 2026-08-24: cs2 ids 28/99, dota id 2). They replay synthetic live data forever
# and never finish — never track, never poll, never ingest.
GRID_TEST_SERIES_IDS = frozenset({"2", "28", "99"})

DATA_DIR = Path(os.environ.get("GRIDBOT_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))


class MissingSecret(RuntimeError):
    """Raised with a secret-FREE message when a required secret is absent."""


# Last good keyring value per secret, with the monotonic time it was
# last CONFIRMED by a healthy keyring. The Secret Service daemon rides
# D-Bus and 2026-08-26 it failed BOTH ways in one afternoon: it crashed
# mid-call (killed the armed engine with an unhandled exception), then its
# locked replacement made every read BLOCK forever behind an unanswered
# GUI unlock prompt (wedged the restarted daemon). So reads run in a
# reaper thread with a hard timeout; an outage or timeout falls back to
# the cache — but only within KEYRING_CACHE_MAX_AGE_S of the last healthy
# confirmation, so a wedged keyring cannot keep an armed engine running on
# a possibly-deleted credential forever (peer review 08-26, finding 1: the
# deletion kill switch must stay bounded even when the keyring is down).
# A healthy keyring answering None (secret deleted) clears the entry.
_SECRET_CACHE: dict[str, tuple[str, float]] = {}
# name -> (worker thread, result box, started_at) of a read whose result is not
# harvested yet
_KEYRING_INFLIGHT: dict[str, tuple[threading.Thread, dict, float]] = {}
# Reads abandoned because their D-Bus call never returned (2026-09-03: one
# such call wedged the capture daemon for ~6h — every later _secret() saw
# "read still blocked" and never asked the keyring again, while a fresh
# process could read the key in 0.1s). After KEYRING_ABANDON_S a blocked
# worker is dropped from the inflight table (it stays a daemon thread) and a
# NEW read is issued; at most KEYRING_MAX_ABANDONED such threads may be alive
# at once — beyond that we keep failing closed and leave recovery to the
# health-check restart.
_ABANDONED: list[threading.Thread] = []
_DEGRADED: set[str] = set()  # names being served from cache (log transitions once)
_SECRET_LOCK = threading.Lock()
KEYRING_TIMEOUT_S = 5.0
KEYRING_CACHE_MAX_AGE_S = 600.0
KEYRING_ABANDON_S = 120.0
KEYRING_MAX_ABANDONED = 8

log = logging.getLogger("gridbot.config")


def _from_cache(name: str, why: str) -> str | None:
    entry = _SECRET_CACHE.get(name)
    if entry is None:
        return None
    val, confirmed_at = entry
    if time.monotonic() - confirmed_at > KEYRING_CACHE_MAX_AGE_S:
        if name in _DEGRADED:
            log.error(
                "secret %s: keyring degraded (%s) and cached value older than "
                "%.0fs — treating as UNAVAILABLE (fail closed)",
                name, why, KEYRING_CACHE_MAX_AGE_S,
            )
        return None
    if name not in _DEGRADED:
        _DEGRADED.add(name)
        log.warning("secret %s: keyring degraded (%s); serving last good "
                    "value from in-process cache", name, why)
    return val


def _secret(name: str) -> str | None:
    env = os.environ.get(name)
    if env:
        register_secret(env)
        return env

    with _SECRET_LOCK:
        prior = _KEYRING_INFLIGHT.get(name)
        if prior is not None:
            prior_worker, prior_box, started_at = prior
            if prior_worker.is_alive():
                _ABANDONED[:] = [t for t in _ABANDONED if t.is_alive()]
                if (time.monotonic() - started_at < KEYRING_ABANDON_S
                        or len(_ABANDONED) >= KEYRING_MAX_ABANDONED):
                    # A previous read is still stuck in D-Bus — don't stack
                    # another blocked thread; serve degraded from cache.
                    return _from_cache(name, "read still blocked")
                # Wedged for too long: abandon it and ask the keyring again.
                # A read that succeeds now (the daemon came back) refreshes
                # the cache and un-degrades the process without a restart.
                _ABANDONED.append(prior_worker)
                _KEYRING_INFLIGHT.pop(name, None)
                log.warning("secret %s: abandoning a keyring read blocked for "
                            ">%.0fs; issuing a fresh read (%d abandoned alive)",
                            name, KEYRING_ABANDON_S, len(_ABANDONED))
            else:
                # Harvest a late completion: a keyring that answers slower than
                # the timeout but SUCCEEDS must still refresh the cache, or a
                # consistently-slow-but-healthy keyring disarms the engine at
                # the cache age bound (code-review f15).
                _KEYRING_INFLIGHT.pop(name, None)
                val = _finish_read(name, prior_box)
                if val is not None:
                    return val

        box: dict[str, object] = {}

        def _read() -> None:
            try:
                import keyring  # lazy so tests without a keyring backend still import config

                box["val"] = keyring.get_password(KEYRING_SERVICE, name)
            except Exception as exc:
                box["exc"] = exc

        worker = threading.Thread(target=_read, daemon=True, name=f"keyring:{name}")
        _KEYRING_INFLIGHT[name] = (worker, box, time.monotonic())
        worker.start()
        worker.join(KEYRING_TIMEOUT_S)
        if worker.is_alive():
            return _from_cache(name, "read timed out")
        _KEYRING_INFLIGHT.pop(name, None)
        return _finish_read(name, box)


def _finish_read(name: str, box: dict) -> str | None:
    """Process a completed keyring read's result box (holds _SECRET_LOCK)."""
    if "exc" in box:
        return _from_cache(name, type(box["exc"]).__name__)
    if name in _DEGRADED:
        _DEGRADED.discard(name)
        log.warning("secret %s: keyring healthy again", name)
    val = box.get("val")
    if isinstance(val, str) and val:
        _SECRET_CACHE[name] = (val, time.monotonic())
        register_secret(val)
        return val
    _SECRET_CACHE.pop(name, None)
    return None


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
