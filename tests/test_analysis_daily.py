"""Slow-loop analysis writer: a note must never be silently truncated.

Four consecutive nightly notes (docs/analysis/2026-08-29 .. 2026-09-02) end
mid-sentence. 805ec73 handled the EMPTY-text case (thinking consumed the
budget) but a PARTIAL text with stop_reason == "max_tokens" was written
as-is. The writer must retry on max_tokens even when text is present, and
if the retry is also cut, mark the note as truncated instead of pretending
it is complete.
"""

from __future__ import annotations

from gridbot import config
from gridbot.analysis import daily


class _Resp:
    def __init__(self, text: str, stop: str, status: int = 200) -> None:
        self.status_code = status
        self._text, self._stop = text, stop

    def json(self) -> dict:
        return {"content": [{"type": "text", "text": self._text}], "stop_reason": self._stop}


def _fake_post(script: list[tuple[str, str]]):
    calls: list[int] = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(json["max_tokens"])
        text, stop = script.pop(0)
        return _Resp(text, stop)

    return post, calls


def test_partial_max_tokens_output_is_retried_with_a_bigger_budget(monkeypatch):
    monkeypatch.setattr(config, "require", lambda name: "k")
    post, calls = _fake_post([("TELEGRAM: half a", "max_tokens"),
                              ("TELEGRAM: whole\nREPORT: done.", "end_turn")])
    monkeypatch.setattr(daily.httpx, "post", post)
    text = daily.ask_claude("evidence", 3000)
    assert text == "TELEGRAM: whole\nREPORT: done."
    assert calls == [3000, 9000]


def test_still_truncated_after_retry_is_marked_not_hidden(monkeypatch):
    monkeypatch.setattr(config, "require", lambda name: "k")
    post, _ = _fake_post([("TELEGRAM: a\nREPORT: cut mid", "max_tokens"),
                          ("TELEGRAM: a\nREPORT: still cut mid", "max_tokens")])
    monkeypatch.setattr(daily.httpx, "post", post)
    text = daily.ask_claude("evidence", 3000)
    assert text.startswith("TELEGRAM: a\nREPORT: still cut mid")
    assert daily.TRUNCATION_MARKER in text


def test_complete_output_is_not_retried(monkeypatch):
    monkeypatch.setattr(config, "require", lambda name: "k")
    post, calls = _fake_post([("TELEGRAM: x\nREPORT: y", "end_turn")])
    monkeypatch.setattr(daily.httpx, "post", post)
    assert daily.ask_claude("evidence", 3000) == "TELEGRAM: x\nREPORT: y"
    assert calls == [3000]


def test_truncation_marker_lands_in_the_report_not_the_telegram():
    tg, rep = daily.split_output(
        f"TELEGRAM: digest\nREPORT: body cut\n{daily.TRUNCATION_MARKER}")
    assert tg == "digest"
    assert rep.endswith(daily.TRUNCATION_MARKER)
