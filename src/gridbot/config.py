"""Central config: hosts, rate ceilings, data paths, and the secrets file.

Secrets policy (see SECURITY.md):
- Every secret lives in ONE file of ``NAME=value`` lines, owned by the
  service user, mode 0600 or tighter. Nothing else is a secret source: not
  the environment, not a keyring, not the repo.
- The file is found at ``$GRIDBOT_SECRETS_FILE``, else
  ``$CREDENTIALS_DIRECTORY/gridbot-secrets`` (systemd ``LoadCredential=``),
  else ``~/.config/gridbot/secrets.env``.
- A file that is group/world-accessible, a symlink, or owned by another user
  is refused: every secret then reads as absent, which fails closed.
- The file is re-checked on every read (one ``stat``), so deleting a key
  disarms a running engine at its next cycle.
- Every secret read is registered with the log redactor.

Check without printing values: ``uv run python -m gridbot.config --check``.
"""

from __future__ import annotations

import logging
import os
import re
import stat
import sys
from pathlib import Path

from gridbot.redaction import register_secret

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

# GRID's permanent looping test fixtures (tournament "GRID-TEST", observed live
# 2026-08-24: cs2 ids 28/99, dota id 2). They replay synthetic live data forever
# and never finish — never track, never poll, never ingest.
GRID_TEST_SERIES_IDS = frozenset({"2", "28", "99"})

DATA_DIR = Path(os.environ.get("GRIDBOT_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))


class MissingSecret(RuntimeError):
    """Raised with a secret-FREE message when a required secret is absent."""


class SecretsFileError(RuntimeError):
    """The secrets file exists but is unsafe or malformed (message is value-free)."""


SECRETS_FILE_ENV = "GRIDBOT_SECRETS_FILE"
CREDENTIAL_NAME = "gridbot-secrets"

# Every secret the system knows about. ``--check`` reports presence for these.
SECRET_NAMES: dict[str, str] = {
    "GRID_API_KEY": "GRID Open Access x-api-key (capture)",
    "POLYMARKET_US_KEY_ID": "Polymarket US API key id (armed execution only)",
    "POLYMARKET_US_SECRET_KEY": "Polymarket US API secret key (armed execution only)",
    "GRIDBOT_ESCALATE_SECRET": "HMAC secret for the health escalation webhook (optional)",
}

_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
MAX_SECRETS_BYTES = 64 * 1024
_LOGGED: set[str] = set()

log = logging.getLogger("gridbot.config")


def secrets_path() -> Path:
    explicit = os.environ.get(SECRETS_FILE_ENV)
    if explicit:
        return Path(explicit)
    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if cred_dir:
        return Path(cred_dir) / CREDENTIAL_NAME
    return Path.home() / ".config" / "gridbot" / "secrets.env"


def _parse(text: str) -> dict[str, str]:
    """``NAME=value`` lines. The value is everything after the first ``=``,
    stripped, with one pair of matching quotes removed; there are no inline
    comments. A malformed line or a repeated name refuses the whole file."""
    out: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        name = name.strip()
        if not sep or not _NAME_RE.match(name):
            raise SecretsFileError(f"secrets file line {lineno}: expected NAME=value")
        if name in out:
            raise SecretsFileError(f"secrets file line {lineno}: {name} is set twice")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value:
            out[name] = value
    return out


def load_secrets(path: Path | None = None) -> dict[str, str]:
    """Return a fresh secrets mapping; {} when the file does not exist.

    Read in full on every call, with no cache: the file is tiny, and a cache
    keyed on stat fields can serve a revoked key after a same-size in-place
    rewrite. Raises SecretsFileError when the file is unsafe (symlink, not a
    regular file, wrong owner, any group/other permission bit, oversized) or
    malformed. Never raises anything that carries the file's bytes.
    """
    path = path or secrets_path()
    try:
        # O_NONBLOCK: a FIFO planted at the path must not hang the open.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}
    except OSError as exc:  # ELOOP = symlink; EACCES = not ours
        raise SecretsFileError(f"secrets file {path} cannot be opened safely "
                               f"({type(exc).__name__})") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise SecretsFileError(f"secrets file {path} is not a regular file")
        if st.st_uid not in (os.geteuid(), 0):
            raise SecretsFileError(f"secrets file {path} is owned by uid {st.st_uid}, "
                                   f"not the service user")
        if st.st_mode & 0o077:
            raise SecretsFileError(f"secrets file {path} is mode "
                                   f"{stat.S_IMODE(st.st_mode):04o}; must be 0600 or 0400")
        if st.st_size > MAX_SECRETS_BYTES:
            raise SecretsFileError(f"secrets file {path} is larger than {MAX_SECRETS_BYTES} bytes")
        data = os.read(fd, MAX_SECRETS_BYTES + 1)
    finally:
        os.close(fd)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        # Re-raise without the original: UnicodeDecodeError.object holds the bytes.
        raise SecretsFileError(f"secrets file {path} is not valid UTF-8") from None
    parsed = _parse(text)
    for value in parsed.values():
        register_secret(value)
    return parsed


def _secret(name: str) -> str | None:
    """The named secret, or None if absent or the file is unsafe (fail closed)."""
    try:
        return load_secrets().get(name)
    except (SecretsFileError, OSError) as exc:
        msg = str(exc) if isinstance(exc, SecretsFileError) else (
            f"secrets file {secrets_path()} read failed ({type(exc).__name__})")
        if msg not in _LOGGED:  # log each distinct problem once, never a value
            _LOGGED.add(msg)
            log.error("%s — treating every secret as absent", msg)
        return None


def require(name: str) -> str:
    val = _secret(name)
    if not val:
        raise MissingSecret(
            f"secret {name!r} not available from {secrets_path()} "
            f"(missing, or the file is unsafe — run: python -m gridbot.config --check)"
        )
    return val


def grid_api_key() -> str:
    """GRID Open Access ``x-api-key``. Header-only — NEVER as a URL param."""
    return require("GRID_API_KEY")


def _check() -> int:
    """Report the secrets file's safety and which known names are set. No values."""
    path = secrets_path()
    print(f"secrets file: {path}")
    try:
        found = load_secrets(path)
    except SecretsFileError as exc:
        print(f"  UNSAFE: {exc}")
        return 2
    if not path.exists():
        print("  absent — create it with: install -m 600 /dev/null <path>, then add NAME=value lines")
    for name, desc in SECRET_NAMES.items():
        print(f"  {'set  ' if name in found else 'unset'}  {name}  ({desc})")
    unknown = sorted(set(found) - set(SECRET_NAMES))
    if unknown:
        print(f"  unknown names (ignored): {', '.join(unknown)}")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] != ["--check"]:
        raise SystemExit("usage: python -m gridbot.config --check")
    raise SystemExit(_check())
