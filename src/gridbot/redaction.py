"""Runtime log redaction: any registered secret is masked in every log record."""

from __future__ import annotations

import logging

_SECRETS: set[str] = set()
_INSTALLED = False


def register_secret(value: str) -> None:
    if value and len(value) >= 8:
        _SECRETS.add(value)


def redact_text(text: str) -> str:
    """Replace every registered secret occurring in ``text`` (longest first)."""
    for s in sorted(_SECRETS, key=len, reverse=True):
        if s in text:
            text = text.replace(s, "«redacted»")
    return text


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _SECRETS:
            msg = record.getMessage()
            redacted = msg
            for s in _SECRETS:
                if s in redacted:
                    redacted = redacted.replace(s, "***REDACTED***")
            if redacted is not msg:
                record.msg = redacted
                record.args = ()
        return True


def install_log_redaction() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    logging.getLogger().addFilter(_RedactingFilter())
    for h in logging.getLogger().handlers:
        h.addFilter(_RedactingFilter())
    _INSTALLED = True
