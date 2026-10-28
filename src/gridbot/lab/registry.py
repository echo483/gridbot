"""The hypothesis registry, v2: a hypothesis that a program can run.

The v1 registry (``docs/experiments/registry.jsonl``) stores three keys —
``hypothesis_id``, ``hypothesis_text``, ``run_ts``. The claim, the population and
the falsification bar all live inside that one paragraph of English. It is a
faithful record of what was decided and why, and it is **not executable**: with
no structured bar, nothing in the tree can check whether an instrument actually
tested the registered thing. That is the real reason the search for edge
stalled — the marginal cost of the Nth idea never fell, because idea N needed a
bespoke script and a human reading a paragraph to grade it.

v2 keeps the prose (it is the reasoning, and it must not be lost) and adds the
four fields that make a hypothesis runnable:

- ``population`` — a SQL predicate over the scan input view. What counts as an
  observation is written down and versioned, not implied by a script.
- ``metric`` + ``bar`` + ``direction`` — the falsification rule, as data.
- ``cluster_key`` — what a resampling unit is. Defaults to ``series_id``; a
  number that cannot state its cluster count is not a number.

**Legacy rows are not upgraded.** Fabricating a structured bar from a paragraph
of prose written months earlier is post-hoc selection with extra steps: the
author of the prose did not commit to that SQL, that cluster key and that
threshold. So a v1 row is imported with ``machine_checkable=False``, it keeps
its prose verbatim, and the scan engine refuses to run it. The measurable
improvement this file buys is exactly the count of runnable rows, and inflating
that count would destroy the only claim it makes.

Both files are append-only. A hypothesis is never edited — a change is a new row
with a new id and a ``parent_id`` pointing at the one it amends, which is what
the v1 amendments already do by hand (``…-NOTE3``, ``…-AMEND2``).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

V2_FILENAME = "registry_v2.jsonl"

#: Metrics the scan engine knows how to evaluate. An unknown metric is a
#: registration error, not a runtime surprise: it fails at load, not mid-scan.
KNOWN_METRICS = ("mean_pnl", "hit_rate", "edge_per_obs", "brier")

#: Falsification direction: the claim is "metric DIRECTION bar" and the lane
#: fails when that does not hold.
KNOWN_DIRECTIONS = (">", "<", ">=", "<=")


@dataclass
class Hypothesis:
    """One registered claim, in a form a program can evaluate."""

    id: str
    title: str
    claim: str  # the prose claim — kept, always
    mechanism: str  # why it should be true; a claim without one is a fishing trip
    population: str  # SQL predicate over the scan input view
    metric: str = "mean_pnl"
    bar: float = 0.0
    direction: str = ">"
    cluster_key: str = "series_id"
    n_min: int = 60
    holdout: str | None = None  # SQL predicate selecting the held-out slice
    config_hash: str = ""
    n_trials: int = 1  # trials the search had run when this row was registered
    status: str = "REGISTERED"
    created_at: str = ""
    parent_id: str | None = None
    machine_checkable: bool = True
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()
        if not self.config_hash:
            self.config_hash = self.compute_hash()

    def compute_hash(self) -> str:
        """Content hash of the *claim-relevant* fields.

        The hash is what ties a registry row to an instrument: a strategy binary
        or scan config carries its hash, and a mismatch means the thing that ran
        is not the thing that was registered. Narrative fields (title, created_at,
        status) are excluded so a rename does not invalidate a result.
        """
        payload = {
            "id": self.id,
            "population": self.population,
            "metric": self.metric,
            "bar": self.bar,
            "direction": self.direction,
            "cluster_key": self.cluster_key,
            "n_min": self.n_min,
            "holdout": self.holdout,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def validate(self) -> list[str]:
        """Return the reasons this row cannot be run. Empty list means runnable."""
        problems: list[str] = []
        if not self.machine_checkable:
            return ["legacy row: prose only, no structured bar"]
        if not self.id.strip():
            problems.append("missing id")
        if not self.population.strip():
            problems.append("missing population predicate")
        if self.metric not in KNOWN_METRICS:
            problems.append(f"unknown metric {self.metric!r} (known: {', '.join(KNOWN_METRICS)})")
        if self.direction not in KNOWN_DIRECTIONS:
            problems.append(f"unknown direction {self.direction!r}")
        if self.n_min < 1:
            problems.append("n_min must be >= 1")
        if self.n_trials < 1:
            problems.append("n_trials must be >= 1")
        return problems

    @property
    def runnable(self) -> bool:
        return not self.validate()

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, default=str)

    @classmethod
    def from_dict(cls, row: dict) -> Hypothesis:
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in row.items() if k in allowed})


def legacy_to_hypothesis(row: dict) -> Hypothesis:
    """Import a v1 prose row as a non-runnable hypothesis. Prose is preserved."""
    hid = str(row.get("hypothesis_id") or row.get("id") or "UNKNOWN")
    text = str(row.get("hypothesis_text") or "")
    return Hypothesis(
        id=hid,
        title=hid,
        claim=text,
        mechanism="",
        population="",
        metric="mean_pnl",
        bar=0.0,
        direction=">",
        n_min=0,
        status="LEGACY_PROSE_ONLY",
        created_at=str(row.get("run_ts") or ""),
        machine_checkable=False,
        meta={"source": "registry.jsonl v1"},
    )


def registry_path(base: Path | None = None) -> Path:
    root = base or (Path(__file__).resolve().parents[3] / "docs" / "experiments")
    return root / V2_FILENAME


def load_registry(base: Path | None = None) -> list[Hypothesis]:
    """Every v2 row, in file order. A malformed row raises: the registry is law."""
    path = registry_path(base)
    if not path.exists():
        return []
    out: list[Hypothesis] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(Hypothesis.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError(f"{path}:{lineno}: unreadable registry row: {exc}") from exc
    return out


def append_hypothesis(hyp: Hypothesis, base: Path | None = None) -> Path:
    """Append one row. Refuses to register a row that cannot be run.

    This is the one place where a bad hypothesis is caught: a registration whose
    bar or population is missing fails here, loudly, instead of producing a scan
    that scores nothing and a reader who assumes it scored something.
    """
    problems = hyp.validate()
    if problems:
        raise ValueError(f"refusing to register {hyp.id}: " + "; ".join(problems))
    path = registry_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(hyp.to_json() + "\n")
    return path


def import_legacy(v1_path: Path, base: Path | None = None) -> int:
    """Append every v1 prose row as a ``machine_checkable=False`` hypothesis."""
    if not v1_path.exists():
        return 0
    path = registry_path(base)
    existing = {h.id for h in load_registry(base)}
    written = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for line in v1_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            hyp = legacy_to_hypothesis(json.loads(line))
            if hyp.id in existing:
                continue
            fh.write(hyp.to_json() + "\n")
            existing.add(hyp.id)
            written += 1
    return written


def find(hypothesis_id: str, base: Path | None = None) -> Hypothesis | None:
    for hyp in load_registry(base):
        if hyp.id == hypothesis_id:
            return hyp
    return None


def summary(base: Path | None = None) -> str:
    rows = load_registry(base)
    if not rows:
        return "registry_v2 is empty"
    runnable = [h for h in rows if h.runnable]
    legacy = [h for h in rows if not h.machine_checkable]
    lines = [
        f"{len(rows)} rows: {len(runnable)} runnable, {len(legacy)} legacy prose-only",
        f"{len(rows) - len(runnable) - len(legacy)} rows registered but not runnable",
        "",
    ]
    for h in rows:
        mark = "RUN " if h.runnable else "----"
        lines.append(f"[{mark}] {h.id:<32} {h.status:<24} {h.metric} {h.direction} {h.bar}")
    return "\n".join(lines)
