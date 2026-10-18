"""Out-of-band notifications — one transport, no framework dependency.

Every ops script used to shell out to an external CLI at a hardcoded absolute
path to reach a chat channel. That is a hard dependency on a binary that is not
part of this project, and the call was copy-pasted into four scripts.

This module is the single place a notification leaves the process. Transport is
**configuration, not code**:

* `GRIDBOT_NOTIFY_WEBHOOK` set to an https URL -> messages POST there as JSON.
* nothing configured -> the message goes to stdout, so every script stays useful
  in a terminal and in CI.

`send` never raises. A watchdog that dies because its alert channel is down is
worse than a watchdog that logs and carries on.
"""

from __future__ import annotations

import json
import os
import sys

WEBHOOK_ENV = "GRIDBOT_NOTIFY_WEBHOOK"
DEFAULT_TIMEOUT_S = 5.0
#: Keep a channel's message limit honest rather than truncating silently mid-word.
MAX_BODY = 3500


def webhook_url() -> str | None:
    """Configured transport URL, or None when unset/blank/non-https."""
    url = os.environ.get(WEBHOOK_ENV, "").strip()
    if not url or not url.startswith("https://"):
        return None
    return url


def configured() -> bool:
    """True when a remote transport is available."""
    return webhook_url() is not None


def send(text: str, *, level: str = "info", timeout: float = DEFAULT_TIMEOUT_S) -> bool:
    """Deliver ``text``. Returns True only if it left the process.

    A ``False`` return means it was printed locally, or the POST failed — both
    are non-fatal by design. Callers should log the return value rather than
    branch on it.
    """
    body = text if len(text) <= MAX_BODY else text[: MAX_BODY - 1] + "…"
    url = webhook_url()
    if url is None:
        print(body)
        return False
    try:
        import httpx

        resp = httpx.post(
            url,
            json={"text": body, "level": level},
            timeout=timeout,
        )
        return 200 <= resp.status_code < 300
    except Exception as exc:
        # Deliberate fail-soft (BLE001 is ignored repo-wide for ops code): a
        # dead alert channel must never take down a watchdog.
        print(f"notify: delivery to {WEBHOOK_ENV} failed ({exc}); body follows", file=sys.stderr)
        print(body, file=sys.stderr)
        return False


def send_json(payload: dict, *, timeout: float = DEFAULT_TIMEOUT_S) -> bool:
    """Deliver a structured payload — for machine consumers, not a chat channel."""
    return send(json.dumps(payload, separators=(",", ":")), level="event", timeout=timeout)
