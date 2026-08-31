"""Verdict-analyzer join and summary semantics."""

from __future__ import annotations

from gridbot.instruments.verdict import resolve_instances, summarize


def inst(hyp: str = "H-MANADV1", sid: str = "1", rnd: int = 5, side: str = "ct",
         team: str = "Alpha", ask: float | None = None, **extra) -> dict:
    record = {
        "hypothesis_id": hyp,
        "series_id": sid,
        "map_number": 1,
        "round_number": rnd,
        "side": side,
        "team": team,
        "opponent": "Beta",
        "market": None,
        "books": {},
        **extra,
    }
    if ask is not None:
        record["market"] = {
            "question": f"{team} vs Beta round {rnd} winner?",
            "outcomes": [team, "Beta"],
            "clob_token_ids": ["10", "20"],
        }
        record["books"] = {"10": {"ask": ask, "bid": ask - 0.02}}
    return record


def test_resolution_join_and_pending() -> None:
    oracles = {"1": {(1, 5): "ct"}}
    rows = resolve_instances([inst(rnd=5), inst(rnd=6)], oracles)
    assert rows[0]["resolved"] and rows[0]["won"] is True
    assert not rows[1]["resolved"] and rows[1]["won"] is None


def test_team_ask_resolved_via_outcome_name() -> None:
    oracles = {"1": {(1, 5): "t"}}
    rows = resolve_instances([inst(ask=0.80)], oracles)
    assert rows[0]["team_ask"] == 0.80 and rows[0]["won"] is False


def test_summarize_cells_and_paper_pnl() -> None:
    oracles = {"1": {(1, 5): "ct", (1, 6): "ct"}, "2": {(1, 5): "ct"}}
    rows = resolve_instances(
        [
            inst(rnd=5, ask=0.80),                      # win, eligible (<=0.85): +0.20
            inst(rnd=6, ask=0.90),                      # win, NOT eligible (>0.85)
            inst(sid="2", rnd=5, side="t", team="Beta"),  # loss, no book
            inst(hyp="H-ECONBUY1", rnd=5, gap_bucket=">=10k"),
            inst(hyp="H-MODELENTRY1", rnd=5, threshold=0.75, dedup_extra="ct:0.75"),
        ],
        oracles,
    )
    summary = {s["cell"]: s for s in summarize(rows)}
    manadv = summary["H-MANADV1"]
    assert manadv["n"] == 3 and manadv["series"] == 2 and manadv["resolved"] == 3
    assert manadv["win_rate"] == round(2 / 3, 4)
    assert manadv["paper_entries"] == 1 and manadv["paper_mean_pnl"] == 0.2
    assert "H-ECONBUY1:>=10k" in summary
    assert "H-MODELENTRY1:thr0.75" in summary
    assert summary["H-ECONBUY1:>=10k"]["bar_n"] == 120


def test_match_kind_market_never_prices_a_round_settlement() -> None:
    oracles = {"1": {(1, 5): "ct"}}
    contaminated = inst(rnd=5, ask=0.30)
    contaminated["market"]["question"] = "Will Alpha beat Beta?"  # match book
    rows = resolve_instances([contaminated], oracles)
    assert rows[0]["team_ask"] is None  # counts as unquoted, no fabricated edge
    clean = inst(rnd=5, ask=0.80)
    clean["market"]["question"] = "Alpha vs Beta round 5 winner?"
    rows = resolve_instances([clean], oracles)
    assert rows[0]["team_ask"] == 0.80


# --- H-ROUNDLANES-RESTRUCTURE1: venue-gated fill-PnL clause ----------------


def test_round_lane_zero_quotes_renders_venue_gated() -> None:
    """quoted==0 across all of a lane's cells => the fill-PnL clause renders
    VENUE_GATED / DEFERRED_OPEN (not pass, not fail) and the win rate is
    clause-(i) resolution-tracking only. Per-cell numbers stay untouched."""
    from gridbot.instruments.verdict import (
        VENUE_GATED_STATUS,
        resolve_instances,
        round_lane_status_lines,
        summarize,
    )

    oracles = {"1": {(1, 5): "ct", (1, 6): "ct"}}
    rows = resolve_instances([inst(rnd=5), inst(rnd=6)], oracles)
    summary = summarize(rows)
    assert summary[0]["quoted"] == 0
    assert summary[0]["win_rate"] is not None  # clause (i) still tracked
    lines = round_lane_status_lines(summary)
    assert lines == [f"  H-MANADV1: {VENUE_GATED_STATUS}"]
    assert "no round-book venue" in lines[0]
    assert "clause i resolution-tracking only" in lines[0]


def test_round_lane_with_any_quote_is_not_venue_gated() -> None:
    """Once any cell shows a round-book quote a venue exists and the gate
    lifts — no status override."""
    from gridbot.instruments.verdict import (
        resolve_instances,
        round_lane_status,
        summarize,
    )

    oracles = {"1": {(1, 5): "ct"}}
    rows = resolve_instances([inst(rnd=5, ask=0.80)], oracles)
    summary = summarize(rows)
    assert summary[0]["quoted"] == 1
    assert round_lane_status(summary) == {}


def test_econbuy_gated_only_when_every_cell_is_unquoted() -> None:
    """ECONBUY spans gap-bucket cells; the lane is venue-gated only when ALL
    of its cells are quoted==0."""
    from gridbot.instruments.verdict import (
        resolve_instances,
        round_lane_status,
        summarize,
    )

    oracles = {"1": {(1, 5): "ct", (1, 6): "ct"}}
    mixed = resolve_instances(
        [
            inst(hyp="H-ECONBUY1", rnd=5, gap_bucket=">=10k", ask=0.78),
            inst(hyp="H-ECONBUY1", rnd=6, gap_bucket="<10k"),
        ],
        oracles,
    )
    assert round_lane_status(summarize(mixed)) == {}

    unquoted = resolve_instances(
        [
            inst(hyp="H-ECONBUY1", rnd=5, gap_bucket=">=10k"),
            inst(hyp="H-ECONBUY1", rnd=6, gap_bucket="<10k"),
        ],
        oracles,
    )
    status = round_lane_status(summarize(unquoted))
    assert list(status) == ["H-ECONBUY1"]
    assert status["H-ECONBUY1"].startswith("VENUE_GATED / DEFERRED_OPEN")


def test_non_round_lanes_never_get_venue_gated() -> None:
    """H-MODELENTRY1 is not a round lane; the restructure does not apply."""
    from gridbot.instruments.verdict import (
        resolve_instances,
        round_lane_status,
        summarize,
    )

    oracles = {"1": {(1, 5): "ct"}}
    rows = resolve_instances(
        [inst(hyp="H-MODELENTRY1", rnd=5, threshold=0.75, dedup_extra="ct:0.75")],
        oracles,
    )
    assert round_lane_status(summarize(rows)) == {}
