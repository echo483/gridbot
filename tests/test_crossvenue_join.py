"""Tests for the intl<->PMUS market join and its ORIENTATION check.

The orientation check is the point of this module. Both venues quote a "Yes"
price, but Yes *of what*: intl markets are ["Yes","No"] token pairs under a
"Counter-Strike: A vs B" title, PMUS markets are team-winner books whose sides
map team -> long/short. If intl's Yes resolves to team A and PMUS's Yes to team
B, every basis computed from the pair is sign-inverted -- and it looks like a
beautiful, stable, tradeable signal. This repo has already paid once for exactly
this class of bug (registry H-MAPENTRY1-NOTE7: 15 of 24 armed-night shorts were
posted at the Yes price instead of 1-cost, crossed the book, were venue-rejected,
and the ledger said "placed" the whole time).

So: resolve both venues' Yes to a NAMED TEAM, and refuse the pair when they
cannot be resolved. Never guess.
"""

from __future__ import annotations

import pytest

from gridbot.analysis import crossvenue_join as cj

MATCH_TITLE = "Counter-Strike: G2 Ares vs Bushido Wildcats (BO3) - DACH CS Masters"


def _intl(question: str, outcomes: list[str], *, title: str = MATCH_TITLE,
          cid: str = "0xabc", tokens: list[str] | None = None) -> dict:
    return {
        "event_title": title,
        "question": question,
        "outcomes": outcomes,
        "condition_id": cid,
        "clob_token_ids": tokens or ["tok_yes", "tok_no"],
    }


def _mapping(sides: dict[str, str], *, slug: str = "aec-cs2-g2a-bw-2026-09-06",
             teams: list[str] | None = None, kind: str = "match") -> dict:
    return {
        "type": "grid_pmus_mapping",
        "grid_series_id": "2990189",
        "grid_teams": teams or ["G2 Ares", "Bushido Wildcats"],
        "grid_start": "2026-09-06T08:00:00Z",
        "us_markets": {kind: {"market_slug": slug, "kind": kind, "sides": sides}},
    }


class TestParse:
    def test_parses_teams_and_yes_team_from_a_match_market(self):
        m = cj.parse_intl_market(_intl(MATCH_TITLE, ["G2 Ares", "Bushido Wildcats"]))
        assert m is not None
        assert m.teams == ("G2 Ares", "Bushido Wildcats")
        assert m.yes_team == "G2 Ares"          # outcomes[0] pairs with clob_token_ids[0]
        assert m.yes_token == "tok_yes"
        assert m.kind == "match"

    def test_parses_map_number_from_the_question(self):
        m = cj.parse_intl_market(
            _intl("Counter-Strike: G2 Ares vs Bushido Wildcats - Map 2 Winner",
                  ["G2 Ares", "Bushido Wildcats"]))
        assert m is not None and m.kind == "map" and m.map_number == 2

    def test_rejects_a_prop_market(self):
        """Games Total / Map Handicap / Odd-Even books are not winner books."""
        assert cj.parse_intl_market(_intl("Games Total: O/U 2.5", ["Over", "Under"])) is None
        assert cj.parse_intl_market(
            _intl("Map 1: Odd/Even Total Kills?", ["Odd", "Even"])) is None

    def test_rejects_a_market_whose_outcomes_are_not_the_two_teams(self):
        assert cj.parse_intl_market(_intl(MATCH_TITLE, ["Yes", "No"])) is None


class TestOrientation:
    def test_aligned_when_both_venues_call_the_same_team_yes(self):
        intl = [_intl(MATCH_TITLE, ["G2 Ares", "Bushido Wildcats"])]
        maps = [_mapping({"G2 Ares": "long", "Bushido Wildcats": "short"})]
        joined, refused = cj.join_markets(intl, maps)
        assert refused == []
        assert len(joined) == 1
        assert joined[0].orientation == "aligned"
        assert joined[0].pmus_yes_team == "G2 Ares"

    def test_inverted_is_DETECTED_not_silently_aligned(self):
        """The failure this module exists to prevent: intl Yes = team A while
        PMUS Yes = team B. It must be reported as inverted, never passed through.
        """
        intl = [_intl(MATCH_TITLE, ["G2 Ares", "Bushido Wildcats"])]
        maps = [_mapping({"Bushido Wildcats": "long", "G2 Ares": "short"})]
        joined, _refused = cj.join_markets(intl, maps)
        assert len(joined) == 1
        assert joined[0].orientation == "inverted"
        assert joined[0].pmus_yes_team == "Bushido Wildcats"

    def test_refuses_when_the_yes_team_matches_neither_pmus_side(self):
        intl = [_intl("Counter-Strike: Astralis vs Vitality (BO3) - X",
                      ["Astralis", "Vitality"],
                      title="Counter-Strike: Astralis vs Vitality (BO3) - X")]
        maps = [_mapping({"G2 Ares": "long", "Bushido Wildcats": "short"})]
        joined, refused = cj.join_markets(intl, maps)
        assert joined == []
        assert refused and refused[0]["reason"] == "no_pmus_match"

    def test_refuses_a_one_sided_pmus_book(self):
        intl = [_intl(MATCH_TITLE, ["G2 Ares", "Bushido Wildcats"])]
        maps = [_mapping({"G2 Ares": "long"})]
        joined, refused = cj.join_markets(intl, maps)
        assert joined == []
        assert refused and refused[0]["reason"] == "pmus_sides_incomplete"


class TestSignConvention:
    @pytest.mark.parametrize(
        ("orientation", "intl_yes_price", "expected"),
        [("aligned", 0.62, 0.62), ("inverted", 0.62, 0.38)],
    )
    def test_intl_price_is_converted_to_the_pmus_yes_convention(
            self, orientation, intl_yes_price, expected):
        """PMUS price is ALWAYS the Yes price (registry NOTE7). An inverted pair
        must be flipped to 1-p BEFORE any basis is taken, never after.
        """
        assert cj.intl_price_as_pmus_yes(intl_yes_price, orientation) == pytest.approx(expected)

    def test_unknown_orientation_raises_rather_than_guessing(self):
        with pytest.raises(ValueError):
            cj.intl_price_as_pmus_yes(0.62, "unknown")
