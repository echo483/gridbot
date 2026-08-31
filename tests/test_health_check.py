"""Tests for scripts/health_check.py — the watchdog's own failure modes.

The watchdog is the thing that notices when everything else breaks, so its
own secret read has to survive the failure it is most likely to be reporting.
2026-08-31: gnome-keyring restarted LOCKED, GRID capture failed closed, and
`_escalate` was found reading the webhook secret through a RAW
`keyring.get_password` with no timeout — the exact call conftest.py documents
as able to "block forever behind a GUI prompt". A hang is not an exception,
so its `except Exception` would not have saved it: the 5-minute watchdog
would have wedged permanently at the moment it was needed.

`gridbot.config._secret` already solves this (5s reaper thread, bounded
last-good cache, fail-closed logging). The watchdog must use it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import health_check


def test_escalate_reads_its_secret_through_the_hardened_path(monkeypatch):
    """_escalate must resolve the webhook secret via config._secret, which
    carries the timeout + bounded cache, NOT via a raw keyring call."""
    from gridbot import config as gconfig

    seen: list[str] = []

    def fake_secret(name: str):
        # None -> no secret configured, so escalation is a no-op
        seen.append(name)

    monkeypatch.setattr(gconfig, "_secret", fake_secret)

    # A raw keyring read would blow up loudly instead of being skipped.
    def exploding_get_password(*_a, **_kw):
        raise AssertionError("_escalate used a RAW keyring read, not config._secret")

    fake_keyring = sys.modules["keyring"]
    monkeypatch.setattr(fake_keyring, "get_password", exploding_get_password, raising=False)

    health_check._escalate("gridbot-capture.service", "unit not active after restart")

    assert seen == ["HERMES_WEBHOOK_GRIDBOT_HEALTH_SECRET"]


def test_escalate_posts_a_signed_webhook_when_the_secret_resolves(monkeypatch):
    """With a readable secret the webhook fires, HMAC-V2 signed."""
    import hashlib
    import hmac
    import json as _json

    from gridbot import config as gconfig

    monkeypatch.setattr(gconfig, "_secret", lambda name: "s3cr3t-under-test")

    posted: dict = {}

    class FakeHTTPX:
        @staticmethod
        def post(url, content=None, headers=None, timeout=None):
            posted.update(url=url, content=content, headers=headers)

    monkeypatch.setitem(sys.modules, "httpx", FakeHTTPX)

    health_check._escalate("gridbot-shadow.service", "detail here")

    assert posted["url"].endswith("/webhooks/gridbot-health")
    body = _json.loads(posted["content"])
    assert body == {"service": "gridbot-shadow.service", "detail": "detail here"}
    ts = posted["headers"]["X-Webhook-Timestamp"]
    expected = hmac.new(
        b"s3cr3t-under-test", f"{ts}.{posted['content']}".encode(), hashlib.sha256
    ).hexdigest()
    assert posted["headers"]["X-Webhook-Signature-V2"] == expected


def test_escalate_never_raises_into_the_watchdog(monkeypatch):
    """Escalation is best-effort: a broken transport must not take down the
    5-minute watchdog that called it."""
    from gridbot import config as gconfig

    def boom(_name):
        raise RuntimeError("keyring backend exploded")

    monkeypatch.setattr(gconfig, "_secret", boom)
    health_check._escalate("gridbot-capture.service", "detail")  # must not raise
