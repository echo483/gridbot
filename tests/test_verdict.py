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
        record["market"] = {"outcomes": [team, "Beta"], "clob_token_ids": ["10", "20"]}
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
