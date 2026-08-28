"""Verification-lane accounting (no network): cost math + fail-closed budget."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest  # noqa: E402

from _anthropic_lane import (  # noqa: E402
    budget_check,
    cost_usd,
    spend_to_date,
)


def test_cost_uses_opus5_list_price() -> None:
    # 1M in + 1M out at $5/$25.
    assert cost_usd({"input_tokens": 1_000_000, "output_tokens": 1_000_000},
                    "claude-opus-5") == pytest.approx(30.0)


def test_cost_discounts_cache_reads_and_charges_writes() -> None:
    c = cost_usd({"cache_read_input_tokens": 1_000_000,
                  "cache_creation_input_tokens": 1_000_000}, "claude-opus-5")
    assert c == pytest.approx(5.0 * 0.1 + 5.0 * 1.25)


def test_unknown_model_bills_at_the_expensive_rate() -> None:
    # A budget gate must never UNDER-count an unrecognized model.
    assert cost_usd({"input_tokens": 1_000_000}, "some-new-model") == pytest.approx(5.0)


def test_spend_to_date_sums_and_survives_torn_lines(tmp_path) -> None:
    led = tmp_path / "anthropic.jsonl"
    led.write_text(
        json.dumps({"cost_usd": 1.5}) + "\n"
        + "{not json\n"                              # torn write
        + json.dumps({"cost_usd": "nope"}) + "\n"    # wrong type
        + json.dumps({"cost_usd": 0.25}) + "\n"
    )
    assert spend_to_date(led) == pytest.approx(1.75)


def test_budget_check_refuses_when_exhausted(tmp_path) -> None:
    led = tmp_path / "anthropic.jsonl"
    led.write_text(json.dumps({"cost_usd": 25.0}) + "\n")
    with pytest.raises(RuntimeError, match="REFUSING"):
        budget_check(25.0, led)
    assert budget_check(30.0, led) == pytest.approx(5.0)


def test_missing_ledger_reads_as_zero_spend(tmp_path) -> None:
    assert spend_to_date(tmp_path / "nope.jsonl") == 0.0


def test_reservation_and_settle_sum_to_true_cost(tmp_path) -> None:
    # A killed call leaves only the reservation (conservative over-count);
    # a completed call's two rows must net to exactly the true cost.
    led = tmp_path / "anthropic.jsonl"
    worst, true = 0.80, 0.11
    led.write_text(
        json.dumps({"kind": "reservation", "cost_usd": worst}) + "\n"
        + json.dumps({"kind": "settle", "cost_usd": true - worst,
                      "true_cost_usd": true}) + "\n"
    )
    assert spend_to_date(led) == pytest.approx(true)
