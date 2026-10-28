"""Data-quality gates. A gate that cannot run is reported as not-run, never as passed.

Each gate answers one question about the tape with a number, so the answer is
falsifiable rather than reassuring. The set is deliberately small: these are the
defects that have actually cost this project a wrong published number or a lost
window, expressed as checks that run in seconds over the whole lake.

- ``raw_reconciled``   — every ingested day's Parquet row count equals the lines
  that parsed. Catches a torn file that silently shrank a day's denominator.
- ``round_coherence``  — share of event rows whose scoreboard agrees with the
  round in progress (corpus F1). The leak filter's whole job is to drive this to
  zero; a rise means a transition snapshot is being read as in-round state.
- ``results_unique``   — one resolution row per (series, map, round). A duplicate
  means the oracle was written twice and every downstream count doubles.
- ``snapshot_gaps``    — per series, snapshots further apart than
  ``max_gap_s``. This is the capture-outage detector: the corpus lost three
  windows to keyring death and one to a power-off, all visible here.
- ``key_nulls``        — null share of the columns research filters on. A column
  that is quietly all-NULL reads as "no signal" when it is really "no sensor".
- ``title_known``      — every event row carries a title. An empty title means a
  capture path wrote a snapshot the tape cannot attribute to a game.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from gridbot.lake.ingest import load_manifest
from gridbot.lake.paths import lake_dir as _lake_dir

DEFAULT_MAX_GAP_S = 300.0
KEY_COLUMNS = ("series_id", "title", "ts_ns", "map_number", "round_number")


@dataclass
class Gate:
    """One check: a name, a verdict, and the number behind it."""

    name: str
    passed: bool | None  # None = not evaluated (no input), never silently True
    detail: str
    n: int = 0

    @property
    def ran(self) -> bool:
        return self.passed is not None


@dataclass
class GateReport:
    gates: list[Gate] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True only when every gate ran and passed."""
        return bool(self.gates) and all(g.passed is True for g in self.gates)

    def add(self, gate: Gate) -> None:
        self.gates.append(gate)

    def render(self) -> str:
        lines = []
        for g in self.gates:
            mark = "PASS" if g.passed else ("FAIL" if g.passed is False else "N/R ")
            lines.append(f"[{mark}] {g.name:<18} n={g.n:<8} {g.detail}")
        return "\n".join(lines)


def _sql_str(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _gate_raw_reconciled(manifest: dict) -> Gate:
    files = manifest.get("files", {})
    if not files:
        return Gate("raw_reconciled", None, "no raw files in the manifest")
    bad = {k: v for k, v in files.items() if not v.get("reconciled", False)}
    dropped = sum(int(v.get("dropped", 0)) for v in files.values())
    if bad:
        return Gate(
            "raw_reconciled",
            False,
            f"{len(bad)}/{len(files)} days did not reconcile; {dropped} line(s) dropped overall",
            n=len(files),
        )
    return Gate(
        "raw_reconciled",
        True,
        f"{len(files)} days reconcile; {dropped} torn line(s) dropped by design",
        n=len(files),
    )


def _gate_round_coherence(con: duckdb.DuckDBPyConnection, has_events: bool) -> Gate:
    if not has_events:
        return Gate("round_coherence", None, "no event tape")
    total, bad = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE rounds_played <> round_number - 1) FROM events"
    ).fetchone()
    if not total:
        return Gate("round_coherence", None, "event tape is empty")
    rate = bad / total
    return Gate(
        "round_coherence",
        rate == 0.0,
        f"{bad}/{total} rows are transition snapshots (F1 leak); rate {rate:.4%}",
        n=int(total),
    )


def _gate_results_unique(con: duckdb.DuckDBPyConnection, has_results: bool) -> Gate:
    if not has_results:
        return Gate("results_unique", None, "no resolution table")
    total, distinct = con.execute(
        "SELECT count(*), count(DISTINCT (series_id, map_number, round_number)) FROM round_results"
    ).fetchone()
    dups = int(total) - int(distinct)
    return Gate(
        "results_unique",
        dups == 0,
        f"{dups} duplicate resolution row(s) over {distinct} keys",
        n=int(total),
    )


def _gate_snapshot_gaps(
    con: duckdb.DuckDBPyConnection, has_events: bool, max_gap_s: float
) -> Gate:
    if not has_events:
        return Gate("snapshot_gaps", None, "no event tape")
    rows = con.execute(
        """
        WITH d AS (
            SELECT series_id, ts_ns,
                   ts_ns - lag(ts_ns) OVER (PARTITION BY series_id ORDER BY ts_ns) AS gap_ns
            FROM events
        )
        SELECT count(*) FILTER (WHERE gap_ns > ? * 1e9),
               count(*) FILTER (WHERE gap_ns IS NOT NULL),
               coalesce(max(gap_ns) / 1e9, 0)
        FROM d
        """,
        [max_gap_s],
    ).fetchone()
    gaps, observed, worst = int(rows[0]), int(rows[1]), float(rows[2])
    if observed == 0:
        return Gate("snapshot_gaps", None, "no consecutive snapshots to measure")
    return Gate(
        "snapshot_gaps",
        gaps == 0,
        f"{gaps} gap(s) > {max_gap_s:.0f}s across {observed} intervals; worst {worst:.0f}s",
        n=observed,
    )


def _gate_key_nulls(con: duckdb.DuckDBPyConnection, has_events: bool) -> Gate:
    if not has_events:
        return Gate("key_nulls", None, "no event tape")
    total = con.execute("SELECT count(*) FROM events").fetchone()[0]
    if not total:
        return Gate("key_nulls", None, "event tape is empty")
    worst_col, worst_rate = "—", 0.0
    for col in KEY_COLUMNS:
        nulls = con.execute(f"SELECT count(*) FROM events WHERE {col} IS NULL").fetchone()[0]
        rate = nulls / total
        if rate > worst_rate:
            worst_col, worst_rate = col, rate
    return Gate(
        "key_nulls",
        worst_rate == 0.0,
        f"worst key column {worst_col} is {worst_rate:.4%} null over {total} rows",
        n=int(total),
    )


def _gate_title_known(con: duckdb.DuckDBPyConnection, has_events: bool) -> Gate:
    if not has_events:
        return Gate("title_known", None, "no event tape")
    total, unknown = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE title IS NULL OR title = '' OR title = 'unknown') "
        "FROM events"
    ).fetchone()
    if not total:
        return Gate("title_known", None, "event tape is empty")
    return Gate(
        "title_known",
        int(unknown) == 0,
        f"{unknown}/{total} rows have no attributable title",
        n=int(total),
    )


def run_gates(
    *, base: Path | None = None, max_gap_s: float = DEFAULT_MAX_GAP_S
) -> GateReport:
    """Run every gate over the built lake. Missing inputs report as not-run."""
    root = base or _lake_dir()
    events_path = root / "events" / "events.parquet"
    results_path = root / "events" / "round_results.parquet"
    has_events = events_path.exists()
    has_results = results_path.exists()

    con = duckdb.connect()
    try:
        if has_events:
            con.execute(
                f"CREATE OR REPLACE VIEW events AS SELECT * FROM read_parquet({_sql_str(str(events_path))})"
            )
        if has_results:
            con.execute(
                f"CREATE OR REPLACE VIEW round_results AS "
                f"SELECT * FROM read_parquet({_sql_str(str(results_path))})"
            )
        report = GateReport()
        report.add(_gate_raw_reconciled(load_manifest()))
        report.add(_gate_round_coherence(con, has_events))
        report.add(_gate_results_unique(con, has_results))
        report.add(_gate_snapshot_gaps(con, has_events, max_gap_s))
        report.add(_gate_key_nulls(con, has_events))
        report.add(_gate_title_known(con, has_events))
        return report
    finally:
        con.close()
