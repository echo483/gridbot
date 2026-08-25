"""training_rounds.jsonl → model matrices, under the binding corpus leak filter.

The filter is CORPUS-F1-LEAKFILTER (docs/experiments/registry.jsonl): a row is
trainable only if it is a coherent in-round snapshot, i.e. the scoreboard
counts exactly the rounds resolved BEFORE the labeled round. Rows failing
``rounds_played + 1 == round_number`` against BOTH ``features.round_number``
and the top-level ``round_number`` are post-round transition snapshots whose
features encode the label — they are never trained on and never scored.
Rows missing any of the 20 contract feature keys (pre-schema-extension
history) are dropped too; nothing else is filtered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from gridbot.corpus.rows import FEATURE_NAMES


@dataclass(frozen=True)
class DatasetMeta:
    """Row accounting plus per-kept-row identity, aligned with X/y/groups."""

    total_rows: int
    kept_rows: int
    leak_dropped: int
    missing_key_rows: int
    missing_key_dropped: int
    row_keys: list[str] = field(repr=False)
    captured_at: list[str] = field(repr=False)

    def counts(self) -> dict[str, int]:
        """Accounting summary suitable for metrics.json."""
        return {
            "total_rows": self.total_rows,
            "kept_rows": self.kept_rows,
            "leak_dropped": self.leak_dropped,
            "missing_key_rows": self.missing_key_rows,
            "missing_key_dropped": self.missing_key_dropped,
        }


def _passes_leak_filter(features: dict, top_level_round_number: float) -> bool:
    """Coherent in-round snapshot check; absent keys fail closed."""
    rounds_played = features.get("rounds_played")
    round_number = features.get("round_number")
    if rounds_played is None or round_number is None:
        return False
    return (
        rounds_played + 1 == round_number and rounds_played + 1 == top_level_round_number
    )


def load_dataset(path: str | Path) -> tuple[np.ndarray, np.ndarray, list[str], DatasetMeta]:
    """Load the corpus jsonl into (X, y, groups, meta).

    Args:
        path: training_rounds.jsonl — one JSON row per line with ``features``
            (dict), ``label``, ``series_id``, ``round_number``, ``captured_at``.

    Returns:
        X: float64 array, one column per FEATURE_NAMES entry, in that order.
        y: float64 array of round-win labels (1.0 = CT-side team won).
        groups: per-row ``series_id`` (the only legal split unit).
        meta: DatasetMeta with drop accounting and per-row identity.
    """
    total = leak_dropped = missing_key_rows = missing_key_dropped = 0
    xs: list[list[float]] = []
    ys: list[float] = []
    groups: list[str] = []
    row_keys: list[str] = []
    captured_at: list[str] = []

    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            total += 1
            features = row["features"]
            missing = any(name not in features for name in FEATURE_NAMES)
            if missing:
                missing_key_rows += 1
            if not _passes_leak_filter(features, row["round_number"]):
                leak_dropped += 1
                continue
            if missing:
                missing_key_dropped += 1
                continue
            xs.append([float(features[name]) for name in FEATURE_NAMES])
            ys.append(float(row["label"]))
            groups.append(str(row["series_id"]))
            row_keys.append(str(row["row_key"]))
            captured_at.append(str(row["captured_at"]))

    meta = DatasetMeta(
        total_rows=total,
        kept_rows=len(xs),
        leak_dropped=leak_dropped,
        missing_key_rows=missing_key_rows,
        missing_key_dropped=missing_key_dropped,
        row_keys=row_keys,
        captured_at=captured_at,
    )
    x_arr = np.asarray(xs, dtype=np.float64).reshape(len(xs), len(FEATURE_NAMES))
    return x_arr, np.asarray(ys, dtype=np.float64), groups, meta
