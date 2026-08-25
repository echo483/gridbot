import time
from datetime import UTC, datetime

from gridbot.capture.files import DayWriter
from gridbot.capture.grid_poller import GridCapture

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def _entry(**over):
    base = {
        "title": "cs2",
        "started": False,
        "finished": False,
        "next_poll": 0.0,
        "last_hash": "",
        "last_change": time.monotonic(),
        "stale_flagged": False,
        "scheduled": "2026-08-25T11:00:00+00:00",
    }
    base.update(over)
    return base


def _capture(tmp_path) -> GridCapture:
    return GridCapture(DayWriter(tmp_path, "ss"), DayWriter(tmp_path, "disc"))


def test_prune_drops_finished_and_stale_scheduled(tmp_path):
    g = _capture(tmp_path)
    g.tracked = {
        "done": _entry(finished=True),
        "ghost": _entry(scheduled="2026-08-24T10:00:00+00:00"),  # >12h ago, never started
        "upcoming": _entry(scheduled="2026-08-25T13:00:00+00:00"),
        "live": _entry(started=True),
    }
    g._prune(NOW)
    assert set(g.tracked) == {"upcoming", "live"}


def test_prune_drops_frozen_live_series(tmp_path):
    g = _capture(tmp_path)
    g.tracked = {"frozen": _entry(started=True, last_change=time.monotonic() - 7 * 3600)}
    g._prune(NOW)
    assert not g.tracked


def test_prune_survives_bad_schedule_strings(tmp_path):
    g = _capture(tmp_path)
    g.tracked = {"weird": _entry(scheduled="not-a-date")}
    g._prune(NOW)
    assert "weird" in g.tracked
