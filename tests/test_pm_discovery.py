import json
from datetime import UTC, datetime

import httpx

from gridbot.capture import pm_discovery
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


# --- tag-scoped discovery (2026-09-07 blindness fix) -------------------------
#
# Pinned defect: the old sweep hit the UNFILTERED /events list with offset
# paging capped at 2000. Gamma refuses offset > ~2000 ("use /events/keyset"),
# and the unfiltered list is dominated by politics + mainstream sports, so
# CS2 match books sorted below the reachable ceiling and were NEVER seen:
# data/pm/catalog-20260903.jsonl and -20260906.jsonl hold 151 / 261 CS2 rows
# and every one is the same season-long future. Zero match books, ever.

CS2_EVENT_TITLE = (
    "Counter-Strike: Villainous vs Axial (BO3) - "
    "PGL Masters Bucharest: North American Open Qualifier #2 Playoffs"
)
CS2_TAGS = [
    {"slug": "esports", "label": "Esports"},
    {"slug": "counter-strike-2", "label": "counter strike 2"},
    {"slug": "games", "label": "Games"},
    {"slug": "sports", "label": "Sports"},
]


def _market(n: int, **over) -> dict:
    """A gamma market: clobTokenIds/outcomes arrive as JSON *strings*."""
    m = {
        "question": f"{CS2_EVENT_TITLE} - Map {n} Winner",
        "conditionId": f"0x{n:064x}",
        "outcomes": '["Villainous", "Axial"]',
        "clobTokenIds": json.dumps([f"{n}01", f"{n}02"]),
        "liquidityNum": 0,
        "liquidity": "0",
        "volume24hr": 38.18,
        "closed": False,
    }
    m.update(over)
    return m


def _event(idx: int = 1, *, title: str = CS2_EVENT_TITLE, tags=None, markets=None) -> dict:
    return {
        "id": str(900000 + idx),
        "slug": f"cs2-vil1-axial-2026-09-0{idx}",
        "title": title,
        "startDate": "2026-09-06T07:35:25Z",
        "endDate": "2099-01-01T07:00:00Z",
        "tags": CS2_TAGS if tags is None else tags,
        "markets": [_market(idx)] if markets is None else markets,
    }


def _client(handler) -> httpx.AsyncClient:
    """An httpx client wired to a fake transport — never touches the network."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _pages(*pages: list[dict]):
    """Serve the given pages in offset order; empty list past the end."""
    seen: list[httpx.QueryParams] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params)
        offset = int(request.url.params.get("offset", 0))
        idx = offset // pm_discovery.PAGE_LIMIT
        body = pages[idx] if idx < len(pages) else []
        return httpx.Response(200, json=body)

    return handler, seen


async def test_discovery_uses_the_esports_tag_route():
    """Every sweep request is tag-scoped; the unfiltered firehose is gone."""
    handler, seen = _pages([_event()])
    async with _client(handler) as client:
        await pm_discovery.fetch_esports_catalog(client)
    assert seen, "no request issued"
    assert all(p.get("tag_slug") in pm_discovery.DISCOVERY_TAG_SLUGS for p in seen)
    assert all(p.get("closed") == "false" for p in seen)


async def test_cs2_match_event_is_discovered_with_the_unchanged_row_shape():
    handler, _ = _pages([_event()])
    async with _client(handler) as client:
        rows = await pm_discovery.fetch_esports_catalog(client)
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {
        "event_title",
        "event_slug",
        "end_date",
        "tags",
        "question",
        "condition_id",
        "outcomes",
        "clob_token_ids",
        "liquidity",
        "volume24hr",
    }
    assert row["event_title"] == CS2_EVENT_TITLE
    assert row["clob_token_ids"] == ["101", "102"]
    assert row["outcomes"] == ["Villainous", "Axial"]
    assert "counter-strike-2" in row["tags"]


async def test_closed_markets_and_stale_events_are_still_dropped():
    stale = _event(2, markets=[_market(2)])
    stale["endDate"] = "2020-01-01T00:00:00Z"
    closed = _event(3, markets=[_market(3, closed=True)])
    handler, _ = _pages([stale, closed, _event(4)])
    async with _client(handler) as client:
        rows = await pm_discovery.fetch_esports_catalog(client)
    assert [r["event_slug"] for r in rows] == ["cs2-vil1-axial-2026-09-04"]


async def test_pagination_terminates_at_the_offset_ceiling():
    """A server that never runs out must not spin forever (or raise)."""
    full = [_event(1)] * pm_discovery.PAGE_LIMIT
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=full)

    async with _client(handler) as client:
        rows = await pm_discovery.fetch_esports_catalog(client)
    assert calls["n"] == pm_discovery.MAX_OFFSET // pm_discovery.PAGE_LIMIT
    assert rows  # deduped by event id, but not empty


async def test_offset_validation_error_ends_the_sweep_keeping_what_was_read():
    """Gamma 422s past its offset ceiling; that is end-of-list, not a failure."""
    page0 = [_event(i) for i in range(1, pm_discovery.PAGE_LIMIT + 1)]

    def handler(request: httpx.Request) -> httpx.Response:
        if int(request.url.params.get("offset", 0)) == 0:
            return httpx.Response(200, json=page0)
        return httpx.Response(
            422,
            json={"type": "validation error", "error": "offset too large, use /events/keyset"},
        )

    async with _client(handler) as client:
        rows = await pm_discovery.fetch_esports_catalog(client)
    assert len(rows) == pm_discovery.PAGE_LIMIT


async def test_network_failure_fails_soft():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("gamma down", request=request)

    async with _client(handler) as client:
        assert await pm_discovery.fetch_esports_catalog(client) == []


async def test_server_error_fails_soft_without_partial_churn():
    """A 5xx mid-sweep yields NO information — never a truncated catalog."""
    page0 = [_event(i) for i in range(1, pm_discovery.PAGE_LIMIT + 1)]

    def handler(request: httpx.Request) -> httpx.Response:
        if int(request.url.params.get("offset", 0)) == 0:
            return httpx.Response(200, json=page0)
        return httpx.Response(503, text="bad gateway")

    async with _client(handler) as client:
        assert await pm_discovery.fetch_esports_catalog(client) == []


async def test_non_esports_rows_are_still_filtered_client_side():
    """Backstop for a silently-ignored tag_slug (the us_mapping failure mode)."""
    politics = _event(5, title="Fed decision in September?", tags=[{"slug": "politics"}])
    handler, _ = _pages([politics, _event(6)])
    async with _client(handler) as client:
        rows = await pm_discovery.fetch_esports_catalog(client)
    assert [r["event_slug"] for r in rows] == ["cs2-vil1-axial-2026-09-06"]


def _row(cid: str, *, title: str, tags: list[str], vol: float,
         question: str | None = None) -> dict:
    return {
        "event_title": title,
        "tags": tags,
        "condition_id": cid,
        "question": title if question is None else question,
        "clob_token_ids": [f"{cid}a", f"{cid}b"],
        "volume24hr": vol,
        "liquidity": vol,
    }


def test_cs2_matches_win_the_asset_cap_against_richer_esports(tmp_path):
    """250 fat LoL/poker books must not evict a single CS2 match book."""
    tracker = CatalogTracker(DayWriter(tmp_path, "cat"))
    loud = [
        _row(f"lol{i}", title=f"LoL: T1 vs GenG (BO5) - LCK Playoffs {i}", tags=["esports"], vol=1e6)
        for i in range(250)
    ]
    cs2 = [
        _row(f"cs{i}", title=CS2_EVENT_TITLE, tags=["counter-strike-2", "esports"], vol=0.0)
        for i in range(30)
    ]
    assets = tracker.update(loud + cs2)
    assert len(assets) <= pm_discovery.MAX_ASSETS
    for row in cs2:
        assert set(row["clob_token_ids"]) <= assets


def test_cs2_futures_outrank_non_cs2_but_yield_to_cs2_matches():
    rows = [
        _row("lol", title="LoL: T1 vs GenG (BO5)", tags=["esports"], vol=1e6),
        _row("faze", title="Will FaZe win a Tier 1 event in 2026?", tags=["cs2"], vol=1e3),
        _row("match", title=CS2_EVENT_TITLE, tags=["counter-strike-2"], vol=0.0),
    ]
    assert [r["condition_id"] for r in pm_discovery.rank_for_capture(rows)] == ["match", "faze", "lol"]


def test_winner_markets_outrank_props_inside_the_same_cs2_event():
    """A CS2 match event carries ~11 markets and only ~4 are winner books.

    Measured on the live esports lane 2026-09-07: 465 CS2 match events, 616 OPEN
    winner tokens against 2,402 OPEN prop tokens — 80% of CS2 tokens are Games
    Total / Map Handicap / Odd-Even-Total-Kills books we can never trade. The
    market `question` discriminates them exactly: winner questions carry the
    "Counter-Strike: A vs B" title form, props do not ("Games Total: O/U 2.5").
    Checked against gamma's own `groupItemTitle` over 1,509 open markets: 1,509
    agree, 0 disagree.
    """
    rows = [
        _row("prop", title=CS2_EVENT_TITLE, tags=["counter-strike-2"], vol=1e6,
             question="Map 1: Odd/Even Total Kills?"),
        _row("total", title=CS2_EVENT_TITLE, tags=["counter-strike-2"], vol=1e6,
             question="Games Total: O/U 2.5"),
        _row("map1", title=CS2_EVENT_TITLE, tags=["counter-strike-2"], vol=0.0,
             question=f"{CS2_EVENT_TITLE} - Map 1 Winner"),
        _row("match", title=CS2_EVENT_TITLE, tags=["counter-strike-2"], vol=0.0,
             question=CS2_EVENT_TITLE),
    ]
    ranked = [r["condition_id"] for r in pm_discovery.rank_for_capture(rows)]
    assert ranked[:2] == ["match", "map1"] or ranked[:2] == ["map1", "match"]
    assert set(ranked[2:]) == {"prop", "total"}


def test_props_are_evicted_before_winner_books_under_the_cap(tmp_path):
    """Props must never displace a live winner book, however loud their volume."""
    tracker = CatalogTracker(DayWriter(tmp_path, "cat"))
    props = [
        _row(f"p{i}", title=CS2_EVENT_TITLE, tags=["counter-strike-2"], vol=1e6,
             question="Map 1: Odd/Even Total Kills?")
        for i in range(pm_discovery.MAX_ASSETS)
    ]
    winners = [
        _row(f"w{i}", title=CS2_EVENT_TITLE, tags=["counter-strike-2"], vol=0.0,
             question=f"{CS2_EVENT_TITLE} - Map {i} Winner")
        for i in range(20)
    ]
    assets = tracker.update(props + winners)
    assert len(assets) <= pm_discovery.MAX_ASSETS
    for row in winners:
        assert set(row["clob_token_ids"]) <= assets


def test_max_assets_covers_the_measured_open_winner_slate():
    """616 open CS2 winner tokens measured on the live lane 2026-09-07.

    Pinned so a future reduction of the cap trips a test instead of silently
    truncating the tradeable slate.
    """
    assert pm_discovery.MAX_ASSETS >= 616


def test_major_is_word_bounded_and_does_not_match_majority():
    """`major` unbounded matched "Next Senate Majority Leader?" — 2,773 of the
    10,223 rows in catalog-20260906.jsonl were that one political event.
    """
    assert not pm_discovery.ESPORTS_TITLE_RE.search("Next Senate Majority Leader?")
    assert pm_discovery.ESPORTS_TITLE_RE.search("PGL Major Bucharest 2026")


def test_empty_sweep_holds_the_previous_asset_set(tmp_path):
    """A failed sweep must not tear down every live book subscription."""
    tracker = CatalogTracker(DayWriter(tmp_path, "cat"))
    row = _row("cs1", title=CS2_EVENT_TITLE, tags=["counter-strike-2"], vol=1.0)
    assets = tracker.update([row])
    assert assets == {"cs1a", "cs1b"}
    assert tracker.update([]) == assets
