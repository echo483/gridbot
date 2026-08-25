"""Synthetic checks of the promotion-gate comparison/decision logic."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

_spec = importlib.util.spec_from_file_location(
    "promote_model", Path(__file__).resolve().parents[1] / "scripts" / "promote_model.py"
)
assert _spec is not None and _spec.loader is not None
pm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pm)


class FakeArtifacts:
    """Stands in for ModelArtifacts with a fixed probability vector."""

    def __init__(self, raw: np.ndarray, calibrated: np.ndarray) -> None:
        self.raw = raw
        self.calibrated = calibrated

    def probabilities(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return self.raw, self.calibrated


def _y(n: int = 200) -> np.ndarray:
    """Deterministic half-1/half-0 labels: best constant Brier is 0.25."""
    y = np.zeros(n)
    y[: n // 2] = 1.0
    return y


def _tracking(y: np.ndarray, sharp: float = 0.9, offset: float = 0.05) -> np.ndarray:
    """Label-tracking probabilities: P = offset + sharp * y (Brier << 0.25)."""
    return offset + sharp * y


def test_candidate_strictly_better_brier_is_promote_eligible() -> None:
    y = _y()
    bad = 0.4 * np.ones_like(y)
    served = FakeArtifacts(bad, bad)
    candidate = FakeArtifacts(bad, _tracking(y))
    metrics, promote_ok = pm.compare_models(candidate=candidate, served=served, x=None, y=y)  # type: ignore[arg-type]
    assert promote_ok is True
    assert metrics["candidate"]["calibrated"]["brier"] < metrics["served"]["calibrated"]["brier"]


def test_equal_brier_fails_closed() -> None:
    y = _y()
    p = 0.3 * np.ones_like(y)
    served = FakeArtifacts(p, p)
    candidate = FakeArtifacts(p, p)
    _, promote_ok = pm.compare_models(candidate=candidate, served=served, x=None, y=y)  # type: ignore[arg-type]
    assert promote_ok is False  # strict < required; ties KEEP-OLD


def test_worse_calibrated_brier_keeps_old_even_if_raw_better() -> None:
    y = _y()
    raw = _tracking(y)
    served = FakeArtifacts(raw, _tracking(y))  # well calibrated
    candidate = FakeArtifacts(raw, 0.3 * np.ones_like(y))  # Brier ~0.35
    _, promote_ok = pm.compare_models(candidate=candidate, served=served, x=None, y=y)  # type: ignore[arg-type]
    assert promote_ok is False  # decision uses calibrated Brier only


def test_report_contains_verdict_and_manual_steps(capsys: pytest.CaptureFixture[str]) -> None:
    y = _y()
    served = FakeArtifacts(0.4 * np.ones_like(y), 0.4 * np.ones_like(y))
    candidate = FakeArtifacts(0.4 * np.ones_like(y), _tracking(y))
    metrics, promote_ok = pm.compare_models(candidate=candidate, served=served, x=None, y=y)  # type: ignore[arg-type]
    pm.print_report(metrics, promote_ok)
    out = capsys.readouterr().out
    assert "PROMOTE-ELIGIBLE" in out
    assert "HUMAN acts" in out
    pm.print_report(metrics, False)
    out = capsys.readouterr().out
    assert "KEEP-OLD (fail-closed)" in out
