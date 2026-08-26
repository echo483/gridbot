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
