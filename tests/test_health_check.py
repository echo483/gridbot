"""Tests for scripts/health_check.py — the watchdog's own failure modes.

The watchdog is the thing that notices when everything else breaks, so its
escalation path must never raise into it and must read its webhook secret
through the one secrets source, `gridbot.config._secret`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import health_check


def test_escalate_reads_its_secret_through_the_hardened_path(monkeypatch):
    """_escalate must resolve the webhook secret via config._secret."""
    from gridbot import config as gconfig

    monkeypatch.setenv("GRIDBOT_ESCALATE_URL", "https://example.invalid/webhooks/gridbot-health")
    seen: list[str] = []

    def fake_secret(name: str):
        # None -> no secret configured, so escalation is a no-op
        seen.append(name)

    monkeypatch.setattr(gconfig, "_secret", fake_secret)

    health_check._escalate("gridbot-capture.service", "unit not active after restart")

    assert seen == ["GRIDBOT_ESCALATE_SECRET"]


def test_escalate_is_skipped_without_a_configured_url(monkeypatch):
    """No transport configured -> escalation is a no-op, not a crash."""
    from gridbot import config as gconfig

    monkeypatch.delenv("GRIDBOT_ESCALATE_URL", raising=False)
    monkeypatch.setattr(gconfig, "_secret", lambda name: "s3cr3t-under-test")
    health_check._escalate("gridbot-capture.service", "detail")  # must not raise


def test_escalate_posts_a_signed_webhook_when_the_secret_resolves(monkeypatch):
    """With a URL and a readable secret the webhook fires, HMAC-V2 signed."""
    import hashlib
    import hmac
    import json as _json

    from gridbot import config as gconfig

    monkeypatch.setenv("GRIDBOT_ESCALATE_URL", "https://example.invalid/webhooks/gridbot-health")
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
        raise RuntimeError("secrets backend exploded")

    monkeypatch.setattr(gconfig, "_secret", boom)
    health_check._escalate("gridbot-capture.service", "detail")  # must not raise


def test_stale_grid_restart_only_when_active_and_cooled():
    import health_check as hc

    now = 10_000.0
    stale = hc.GRID_FRESH_S + 60
    assert hc._stale_grid_restart_wanted(stale, True, 0.0, now)
    assert not hc._stale_grid_restart_wanted(60.0, True, 0.0, now)      # fresh
    assert not hc._stale_grid_restart_wanted(stale, False, 0.0, now)    # DOWN branch owns it
    assert not hc._stale_grid_restart_wanted(stale, True, now - 600, now)  # cooldown
    assert hc._stale_grid_restart_wanted(stale, True, now - 3601, now)
