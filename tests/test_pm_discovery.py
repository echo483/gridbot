from datetime import UTC, datetime

from gridbot.capture.files import DayWriter
from gridbot.capture.pm_discovery import CatalogTracker, _as_list, _is_alive, _is_esports

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def test_is_esports_by_tag_and_title():
    assert _is_esports({"tags": [{"slug": "esports"}], "title": "x"})
    assert _is_esports({"tags": [], "title": "Counter-Strike: A vs B (BO3)"})
    assert _is_esports({"tags": [], "title": "LoL: T1 vs GenG"})
    assert not _is_esports({"tags": [{"slug": "politics"}], "title": "Fed decision"})


def test_is_alive_drops_stale():
    assert _is_alive({"endDate": "2026-08-25T00:00:00Z"}, NOW)
    assert not _is_alive({"endDate": "2026-03-10T00:00:00Z"}, NOW)
    assert _is_alive({}, NOW)


def test_as_list_handles_gamma_json_strings():
    assert _as_list('["1","2"]') == ["1", "2"]
    assert _as_list(["3"]) == ["3"]
    assert _as_list(None) == []
    assert _as_list("not json") == []


def test_catalog_tracker_dedupes_and_returns_assets(tmp_path):
    w = DayWriter(tmp_path, "cat")
    tracker = CatalogTracker(w)
    row = {
        "condition_id": "0xabc",
        "clob_token_ids": ["11", "22"],
        "volume24hr": 5.0,
        "liquidity": 10.0,
    }
    assets = tracker.update([row])
    assert assets == {"11", "22"}
    n_after_first = w.written
    tracker.update([row])  # unchanged -> no new catalog row
    assert w.written == n_after_first
    tracker.update([{**row, "volume24hr": 9.0}])  # changed -> re-emitted
    assert w.written == n_after_first + 1
