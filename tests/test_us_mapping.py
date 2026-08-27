"""Tests for executor/us_mapping.py — pure mapping functions, no network."""

from __future__ import annotations

from gridbot.executor.us_mapping import (
    join_series,
    map_number_of,
    match_grid_to_event,
    pmus_team_names,
    us_market_kind,
    us_markets_for_event,
)


def _grid(sid: str, a: str, b: str, start: str) -> dict:
    return {
        "id": sid,
        "startTimeScheduled": start,
        "teams": [{"baseInfo": {"name": a}}, {"baseInfo": {"name": b}}],
    }


def _event(slug: str, a: str, b: str, start: str, series: str = "cs2") -> dict:
    return {
        "slug": slug,
        "title": f"{a} vs {b}",
        "seriesSlug": series,
        "startTime": start,
        "teams": [{"name": a}, {"name": b}],
    }


class TestMatch:
    def test_exact_teams_same_start(self) -> None:
        g = _grid("1", "Team Nemesis", "1WIN", "2026-08-26T18:00:00+00:00")
        e = _event("cs2-nem-1win-2026-08-26", "Team Nemesis", "1WIN", "2026-08-26T18:00:00Z")
        m = match_grid_to_event(g, e)
        assert m is not None
        assert m["score"] >= 0.99
        assert m["pmus_event_slug"] == "cs2-nem-1win-2026-08-26"

    def test_org_suffix_normalization(self) -> None:
        # GRID "Nemesis" should still cover PMUS "Team Nemesis" (stopword).
        g = _grid("1", "Nemesis", "1WIN", "2026-08-26T18:00:00+00:00")
        e = _event("x", "Team Nemesis", "1WIN", "2026-08-26T18:00:00Z")
        assert match_grid_to_event(g, e) is not None

    def test_outside_time_window_refused(self) -> None:
        g = _grid("1", "Team Nemesis", "1WIN", "2026-08-26T18:00:00+00:00")
        e = _event("x", "Team Nemesis", "1WIN", "2026-08-27T18:00:00Z")
        assert match_grid_to_event(g, e) is None

    def test_one_team_mismatch_refused(self) -> None:
        g = _grid("1", "Team Nemesis", "Galorys", "2026-08-26T18:00:00+00:00")
        e = _event("x", "Team Nemesis", "1WIN", "2026-08-26T18:00:00Z")
        assert match_grid_to_event(g, e) is None

    def test_non_esports_series_refused(self) -> None:
        g = _grid("1", "A1", "B2", "2026-08-26T18:00:00+00:00")
        e = _event("x", "A1", "B2", "2026-08-26T18:00:00Z", series="nfl-2025")
        assert match_grid_to_event(g, e) is None

    def test_skew_lowers_score(self) -> None:
        g = _grid("1", "Team Nemesis", "1WIN", "2026-08-26T18:00:00+00:00")
        near = _event("near", "Team Nemesis", "1WIN", "2026-08-26T18:00:00Z")
        far = _event("far", "Team Nemesis", "1WIN", "2026-08-26T20:30:00Z")
        m_near = match_grid_to_event(g, near)
        m_far = match_grid_to_event(g, far)
        assert m_near is not None and m_far is not None
        assert m_near["score"] > m_far["score"]


class TestJoin:
    def test_best_event_wins_and_one_per_series(self) -> None:
        # Regression for the review-caught dead loop: join_series must
        # actually iterate events and pick the best-scoring one.
        g = _grid("101", "Team Nemesis", "1WIN", "2026-08-26T18:00:00+00:00")
        near = _event("near", "Team Nemesis", "1WIN", "2026-08-26T18:00:00Z")
        far = _event("far", "Team Nemesis", "1WIN", "2026-08-26T20:00:00Z")
        out = join_series([g], [far, near])
        assert len(out) == 1
        assert out[0]["pmus_event_slug"] == "near"

    def test_test_series_excluded(self) -> None:
        g = _grid("28", "Team Nemesis", "1WIN", "2026-08-26T18:00:00+00:00")
        e = _event("x", "Team Nemesis", "1WIN", "2026-08-26T18:00:00Z")
        assert join_series([g], [e]) == []

    def test_empty_inputs(self) -> None:
        assert join_series([], []) == []


class TestMarketTyping:
    def test_kinds(self) -> None:
        assert us_market_kind({"sportsMarketType": "esports_match_winner"}) == "match"
        assert us_market_kind({"sportsMarketType": "esports_map_winner_2"}) == "map"
        assert us_market_kind({"sportsMarketType": "esports_map_total_rounds_1"}) == "total"
        assert us_market_kind({"sportsMarketType": "esports_series_map_handicap"}) == "handicap"
        assert us_market_kind({"sportsMarketType": "weird"}) == "other"

    def test_map_number(self) -> None:
        assert map_number_of({"sportsMarketType": "esports_map_winner_1"}) == 1
        assert map_number_of({"sportsMarketType": "esports_match_winner"}) is None

    def test_markets_for_event_sides(self) -> None:
        event = {
            "markets": [
                {
                    "slug": "s-match",
                    "sportsMarketType": "esports_match_winner",
                    "marketSides": [
                        {"team": {"name": "Team Nemesis"}, "long": True},
                        {"team": {"name": "1WIN"}, "long": False},
                    ],
                },
                {
                    "slug": "s-map1",
                    "sportsMarketType": "esports_map_winner_1",
                    "marketSides": [
                        {"team": {"name": "Team Nemesis"}, "long": True},
                        {"team": {"name": "Team Nemesis"}, "long": False},
                    ],
                },
                {"slug": "s-total", "sportsMarketType": "esports_series_total_maps"},
            ]
        }
        out = us_markets_for_event(event)
        assert set(out) == {"match", "map1"}  # totals/handicaps excluded
        assert out["match"]["sides"] == {"Team Nemesis": "long", "1WIN": "short"}
        assert out["map1"]["sides"] == {"Team Nemesis": "long"}


class TestTeamNames:
    def test_from_teams_field(self) -> None:
        assert pmus_team_names({"teams": [{"name": "A"}, {"name": "B"}]}) == ["A", "B"]

    def test_fallback_to_title(self) -> None:
        assert pmus_team_names({"title": "Alpha vs Beta"}) == ["Alpha", "Beta"]

    def test_empty(self) -> None:
        assert pmus_team_names({}) == []


class TestLiveWindowTeamNames:
    def test_live_and_imminent_series_only(self) -> None:
        from gridbot.executor.us_mapping import live_window_team_names

        now = 1_000_000.0
        h = 3600
        series = [
            _grid("1", "Alpha", "Bravo", "1970-01-12T13:46:40Z"),      # ~ now
            _grid("2", "Charlie", "Delta", "1970-01-12T05:00:00Z"),    # way past
            _grid("3", "Echo", "Foxtrot", "1970-01-13T13:00:00Z"),     # far future
        ]
        # give series 1 a start exactly at `now`, 2 at now-10h, 3 at now+24h
        import datetime as dt

        def iso(ts: float) -> str:
            return dt.datetime.fromtimestamp(ts, dt.UTC).isoformat()

        series[0]["startTimeScheduled"] = iso(now)
        series[1]["startTimeScheduled"] = iso(now - 10 * h)
        series[2]["startTimeScheduled"] = iso(now + 24 * h)
        names = live_window_team_names(series, now)
        assert names == ("Alpha", "Bravo")

    def test_dedup_and_test_series_excluded(self) -> None:
        from gridbot.executor.us_mapping import live_window_team_names
        import datetime as dt

        now = 2_000_000.0
        iso = dt.datetime.fromtimestamp(now, dt.UTC).isoformat()
        series = [
            _grid("10", "Alpha", "Bravo", iso),
            _grid("11", "Alpha", "Gamma", iso),   # Alpha repeats
            _grid("28", "CS2-1", "CS2-2", iso),   # GRID test fixture id
        ]
        names = live_window_team_names(series, now)
        assert names == ("Alpha", "Bravo", "Gamma")


class TestTeamSearchLane:
    class _Client:
        """search.query returns a LIVE event only for team-name queries —
        mirrors the venue behavior verified 2026-08-27 (generic keyword
        lanes drop in-play events)."""

        def __init__(self) -> None:
            outer = self
            self.queries: list[str] = []

            class _Search:
                def query(self, params):
                    q = params["query"]
                    outer.queries.append(q)
                    if q == "NuTorious":
                        return {"events": [_event(
                            "cs2-nt-ot-2026-08-27", "NuTorious",
                            "Overtake Sector", "2026-08-27T01:00:00Z")]}
                    return {"events": []}

            class _Events:
                def list(self, params):
                    return {"events": []}

            self.search = _Search()
            self.events = _Events()

    def test_team_queries_surface_live_events(self) -> None:
        from gridbot.executor.us_mapping import fetch_pmus_esports_events

        client = self._Client()
        events = fetch_pmus_esports_events(
            client, ("NuTorious", "Overtake Sector", "TBD-1", "ab", "NuTorious")
        )
        assert [e["slug"] for e in events] == ["cs2-nt-ot-2026-08-27"]
        # TBD-* and short names never queried; duplicates collapsed
        team_qs = [q for q in client.queries
                   if q not in ("cs2", "esports", "valorant", "league of legends")]
        assert team_qs == ["NuTorious", "Overtake Sector"]
