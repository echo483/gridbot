"""H-MODELENTRY1 crossing detector: served round-model threshold crossings
during unresolved rounds.

The served predictor is the PINNED artifact ``data/models/roundwin-v1``
(model.txt trimmed to the benchmarked best_iteration; Platt
``sigmoid(a * margin + b)``) — the exact predictor behind the holdout
benchmark in docs/MODEL.md. Swapping artifacts requires a registry amendment.
Cells are frozen in registry entry H-MODELENTRY1-CELLS before any instance was
recorded: thresholds × side, FIRST crossing per (series, map, round, side,
threshold), CORPUS-F1 coherence guard, clock ≥ 10s. q is a quant model; no
LLM ever (iron rail 1).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable

from gridbot import config
from gridbot.corpus import rows as r

THRESHOLDS = (0.65, 0.75, 0.85)
MIN_CLOCK_S = 10.0
MODEL_DIR = config.DATA_DIR / "models" / "roundwin-v1"


class CrossingTracker:
    """First-crossing detection over a served (or, in tests, scripted) predictor."""

    def __init__(self, predict: Callable[[dict[str, float]], float]) -> None:
        self.predict = predict
        self._last: dict[tuple, float] = {}
        self._fired: set[tuple] = set()

    def observe(self, state: dict) -> list[dict]:
        """Crossing instances triggered by this snapshot (usually empty)."""
        if state.get("finished"):
            return []
        game = r.current_game(state)
        if game is None:
            return []
        ct_node, t_node = r.sided_teams(game)
        ct_name = str((ct_node or {}).get("name") or "")
        t_name = str((t_node or {}).get("name") or "")
        ct_score = int((ct_node or {}).get("score") or 0)
        t_score = int((t_node or {}).get("score") or 0)
        winners = r.round_winners(game)
        round_number = len(winners) + 1
        map_number = int(game.get("sequenceNumber") or 1)
        round_segs = [
            s for s in game.get("segments") or [] if str(s.get("type") or "").lower() == "round"
        ]
        seg_round = int(round_segs[-1].get("sequenceNumber") or 1) if round_segs else 1
        if ct_score + t_score != round_number - 1 or seg_round != round_number:
            return []  # CORPUS-F1 coherence guard: never predict on transition state
        clock = (game.get("clock") or {}).get("currentSeconds")
        if clock is not None and clock < MIN_CLOCK_S:
            return []

        q_ct = self.predict(r.build_features(game))
        key = (str(state.get("id") or ""), map_number, round_number)
        prev = self._last.get(key)
        self._last[key] = q_ct
        if prev is None:
            return []

        out: list[dict] = []
        for threshold in THRESHOLDS:
            for side, q_prev, q_now, team, opponent in (
                ("ct", prev, q_ct, ct_name, t_name),
                ("t", 1.0 - prev, 1.0 - q_ct, t_name, ct_name),
            ):
                cell = (*key, side, threshold)
                if cell in self._fired or not q_prev < threshold <= q_now:
                    continue
                self._fired.add(cell)
                out.append(
                    {
                        "hypothesis_id": "H-MODELENTRY1",
                        "series_id": key[0],
                        "map_number": map_number,
                        "round_number": round_number,
                        "clock_seconds": clock,
                        "side": side,
                        "team": team,
                        "opponent": opponent,
                        "threshold": threshold,
                        "q_prev": round(q_prev, 4),
                        "q": round(q_now, 4),
                        "fair_value": round(q_now, 4),
                        "dedup_extra": f"{side}:{threshold}",
                        "state": {
                            "ct": {"name": ct_name, "score": ct_score},
                            "t": {"name": t_name, "score": t_score},
                        },
                    }
                )
        return out


def load_served() -> CrossingTracker | None:
    """The pinned artifact as a tracker; None when no artifact is deployed."""
    model_file = MODEL_DIR / "model.txt"
    if not model_file.exists():
        return None
    import lightgbm as lgb
    import numpy as np

    booster = lgb.Booster(model_file=str(model_file))
    calib = json.loads((MODEL_DIR / "calibration.json").read_text())
    a, b = float(calib["a"]), float(calib["b"])
    names = tuple(json.loads((MODEL_DIR / "feature_names.json").read_text()))
    if names != r.FEATURE_NAMES:
        raise ValueError("served model feature order violates the FEATURE_NAMES contract")

    def predict(features: dict[str, float]) -> float:
        x = np.asarray([[features[k] for k in r.FEATURE_NAMES]])
        margin = float(booster.predict(x, raw_score=True)[0])
        return 1.0 / (1.0 + math.exp(-(a * margin + b)))

    return CrossingTracker(predict)
