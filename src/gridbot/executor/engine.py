"""Executor engine: tail shadow instances, emit capped maker intents.

dry_run (default): every intent is appended to the intent ledger with
status "dry_run" — the byte-identical decision path a live session takes,
minus the wire call. live (armed only): the same intent goes through the
Polymarket US wire layer (pmus.py) and the ack/error is recorded next to it.

The engine re-checks the arming gate EVERY cycle: pulling keys from the
keyring or unsetting GRIDBOT_LIVE mid-flight drops it back to dry_run on
the next tick. It never mutates arming state itself (iron rail 3).

Run: uv run python -m gridbot.executor.engine [--once]
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from gridbot import config
from gridbot.executor import fills as fills_mod
from gridbot.executor import ledger as ledger_mod
from gridbot.executor import limits, pmus, reconcile
from gridbot.executor.intents import (
    OrderIntent,
    build_intent,
    build_us_intent,
    us_band_gated,
)
from gridbot.instruments.shadow import Follow

log = logging.getLogger("gridbot.executor")

CYCLE_S = 2.0
RECONCILE_EVERY = 30  # armed only: venue orders.list vs ledger every ~60s
MAKER_TTL_S = 150.0  # M5: pull unfilled maker quotes — a fair-value quote
# older than ~2 rounds is stale against a live map and pure adverse selection


def _decision_row(
    intent: OrderIntent, record: dict, status: str, live: bool,
    order_id: str | None = None,
) -> dict:
    """One ledger row for a decision on ``intent`` (placed, pending, skipped).

    Every decision the engine takes — wire attempt, outcome, or skip —
    is ledgered in this one shape.
    """
    row = intent.record()
    row.update(
        {
            "status": status,
            "order_id": order_id,
            "p_map": record.get("p_map"),
            "ts_ns": time.time_ns(),
            "live": live,
        }
    )
    return row


class IntentLedger:
    """Append-only day-file ledger under data/executor/."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or config.DATA_DIR) / "executor"
        self.root.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict) -> None:
        day = f"{datetime.now(UTC):%Y%m%d}"
        path = self.root / f"intents-{day}.jsonl"
        with open(path, "a") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")


class Engine:
    """One instance-file pass per cycle; dedup within process lifetime."""

    def __init__(self, ledger: IntentLedger | None = None) -> None:
        self.ledger = ledger or IntentLedger()
        self.follow = Follow(config.DATA_DIR / "shadow", "instances", from_start=False)
        # H-MAPENTRY1 lane: qualifying mapentry rows already carry the US
        # market slug/side + the frozen entry rule evaluated against a real
        # book — the executor only converts, caps and dedups them.
        self.follow_us = Follow(config.DATA_DIR / "shadow", "mapentry", from_start=False)
        self.seen: set[str] = set()
        self._cycles = 0
        # Orders THIS process placed and has not yet released: order_id ->
        # {slug, dedup_key, placed_mono}. The TTL sweep cancels them when
        # stale. (Orders from a previous armed session are the reconcile
        # pass's job — they show up as ledger-open vs venue drift.)
        self.resting: dict[str, dict] = {}
        # Open exposure = placed-but-unsettled intents, recomputed from the
        # ledger each cycle (S3: survives restarts, releases on settle —
        # never a process-local monotonic counter). Incremented in-cycle on
        # each new "placed" for immediacy between recomputes. Venue-side
        # reconciliation (orders.list) is a pre-scaling item: LIVE_FIRE.md.
        self.open_count = 0
        self._daily_loss = 0.0
        # Last reconcile report actually appended: repeat passes with an
        # identical diff neither re-warn nor re-append (the first armed night
        # wrote 558 near-identical rows for one long-cancelled order).
        self._last_reconcile: dict | None = None
        # Order ids proven terminal-with-no-fill by the fills sync — never
        # re-fetched. Fills/settles found on venue become ledger rows so
        # exposure and the daily-loss cap bind on real numbers (S3).
        self._fill_checked: set[str] = set()
        # H-MAPENTRY1-REENTRY1 (frozen 08-26, user-approved): ledger-derived
        # per-key placed counts (2 lifetime max) and fill-bearing keys (a
        # filled book never releases). Refreshed each cycle's ledger read.
        # _filled_orders is ORDER-scoped for the sweep's cancel-skip — under
        # re-entry one key holds two orders and entry 1's fill must never
        # make the sweep abandon unfilled entry 2 (qwen review f1, BLOCKER).
        self._placed_counts: dict[str, int] = {}
        self._filled_keys: set[str] = set()
        self._filled_orders: set[str] = set()
        # Keys whose prior entries are ALL venue-confirmed clean-cancelled
        # (no fill/settle/gone) — the only keys a fresh process may
        # re-enter (code-review f3: frozen never-release conditions must
        # survive restarts via the ledger, not process memory).
        self._reentry_ok: set[str] = set()
        # Clean cancels awaiting VENUE confirmation of zero fill before the
        # re-entry release fires: cancel status alone lies (production
        # evidence: three FULL fills reported "cancelled"), and terminal-only
        # fill recording means the fill row cannot exist at sweep time — so
        # releasing at cancel time would hand every partially-filled book a
        # re-entry (qwen review f2). order_id -> dedup_key.
        self._pending_release: dict[str, str] = {}

    def handle(self, record: dict, armed: limits.ArmingState) -> str | None:
        """Process one shadow record; returns the outcome status or None."""
        kind = record.get("type")
        if kind not in ("instance", "mapentry"):
            return None
        if str(record.get("series_id")) in config.GRID_TEST_SERIES_IDS:
            return None
        if kind == "mapentry":
            intent = build_us_intent(record, limits.max_stake_usd())
        else:
            intent = build_intent(record, limits.max_stake_usd())
        if intent is None:
            return None
        if intent.dedup_key in self.seen:
            return None
        # REENTRY1 hard cap: at most 2 lifetime placed entries per key,
        # counted from the ledger so it survives restarts. Ledgered so the
        # measurement population keeps its denominator (glm review f11).
        prior = self._placed_counts.get(intent.dedup_key, 0)
        if prior >= 2:
            self._skip_row(intent, record, "skipped:reentry_cap", armed)
            return "skipped:reentry_cap"
        # Restart-safe never-release gate (code-review f3): a key with ANY
        # prior entry re-enters only when the ledger proves every prior
        # order clean-cancelled and unfilled.
        if prior >= 1 and intent.dedup_key not in self._reentry_ok:
            self._skip_row(intent, record, "skipped:reentry_blocked", armed)
            return "skipped:reentry_blocked"
        # Band gate (executor risk policy, frozen pre-first-fire): record-only
        # skip, dedup NOT consumed — a later in-band crossing on the same book
        # may still enter. Applies identically in dry_run so the armed
        # decision path stays byte-identical.
        if kind == "mapentry" and us_band_gated(record.get("p_map")):
            self.ledger.append(
                _decision_row(intent, record, "skipped:band_gate", armed.live)
            )
            log.info("band gate: %s p_map=%s held out of armed band",
                     intent.dedup_key, record.get("p_map"))
            return "skipped:band_gate"
        # Capacity skips do NOT consume the dedup key (M6). They ARE
        # ledgered: a silently dropped would-have-traded row is selection
        # bias in the measurement record (glm review f11).
        if self.open_count >= limits.max_open_positions():
            self._skip_row(intent, record, "skipped:capacity", armed)
            return "skipped:capacity"
        if self._daily_loss >= limits.max_daily_loss_usd():
            self._skip_row(intent, record, "skipped:daily_loss", armed)
            return "skipped:daily_loss"
        order_id = None
        if armed.live:
            # Write-ahead row BEFORE the wire call (glm review f7): a create
            # the ledger cannot record must not happen — and a crash between
            # venue ack and outcome append leaves the pending row as evidence
            # (counted toward the REENTRY1 cap until an outcome supersedes).
            pending = _decision_row(intent, record, "wire_pending", True)
            try:
                self.ledger.append(pending)
            except Exception:
                log.critical("ledger unwritable — REFUSING to place %s "
                             "(fail closed; measurement outranks trading)",
                             intent.dedup_key)
                return None
            self.seen.add(intent.dedup_key)
            status, order_id = pmus.place(intent)
            if status == "placed":
                self.open_count += 1
                self._placed_counts[intent.dedup_key] = (
                    self._placed_counts.get(intent.dedup_key, 0) + 1
                )
                self.resting[str(order_id)] = {
                    "slug": intent.market_slug,
                    "dedup_key": intent.dedup_key,
                    "placed_mono": time.monotonic(),
                }
        else:
            self.seen.add(intent.dedup_key)
            status = "dry_run"
        row = _decision_row(intent, record, status, armed.live, order_id)
        try:
            self.ledger.append(row)
        except Exception:
            # The order (if any) is live and tracked in-memory — the TTL
            # sweep still guards it — but the ledger just lost a row: say
            # so at CRITICAL, never as "bad shadow record" (glm review f7).
            log.critical("ledger append FAILED for %s outcome %s — venue "
                         "state diverges from ledger", intent.dedup_key, status)
        log.info("intent %s %s @%.4f x%.2f -> %s", intent.hypothesis_id,
                 intent.team, intent.price, intent.shares, status)
        return status

    def _refresh_ledger_state(self, records: list[dict]) -> None:
        """Derive REENTRY1 state from the ledger (restart-safe by design).

        Cap accounting (glm f1/f6 + code-review f9): every wire attempt
        writes a wire_pending row first; pendings pair with the outcome
        that FOLLOWS them in file order, so a dangling pending (crash
        between ack and outcome) consumes a slot and historical pre-WAL
        rows cannot absorb it. Also derives the never-release entry gate
        (code-review f3): a key re-enters only when every placed order on
        it is clean-cancelled with nothing filled, settled, or gone.
        """
        hard: dict[str, int] = {}
        open_pend: dict[str, int] = {}
        filled: set[str] = set()
        filled_orders: set[str] = set()
        settled: set[str] = set()
        bad_cancel: set[str] = set()  # gone_before_cancel / error cancels
        placed_by_key: dict[str, set[str]] = {}
        clean_orders: set[str] = set()
        for row in records:
            if not isinstance(row, dict):
                continue
            key = row.get("dedup_key")
            if not isinstance(key, str) or not key:
                continue
            t = row.get("type")
            s = str(row.get("status") or "")
            if t == "fill":
                filled.add(key)
                if row.get("order_id"):
                    filled_orders.add(str(row["order_id"]))
            elif t == "settle":
                settled.add(key)
            elif t == "cancel":
                if s == "cancelled" and row.get("order_id"):
                    clean_orders.add(str(row["order_id"]))
                elif s != "cancelled":
                    bad_cancel.add(key)
            elif t == "order_intent":
                if s == "wire_pending":
                    open_pend[key] = open_pend.get(key, 0) + 1
                elif s == "placed" or s.startswith("error:"):
                    hard[key] = hard.get(key, 0) + 1
                    if open_pend.get(key, 0) > 0:
                        open_pend[key] -= 1
                    if s == "placed" and row.get("order_id"):
                        placed_by_key.setdefault(key, set()).add(
                            str(row["order_id"])
                        )
                elif s.startswith("rejected:") and open_pend.get(key, 0) > 0:
                    open_pend[key] -= 1
        self._placed_counts = {
            k: hard.get(k, 0) + open_pend.get(k, 0)
            for k in set(hard) | set(open_pend)
        }
        self._filled_keys = filled
        self._filled_orders = filled_orders
        self._reentry_ok = {
            k for k, oids in placed_by_key.items()
            if oids <= clean_orders
            and k not in filled and k not in settled and k not in bad_cancel
        }

    def _skip_row(self, intent: OrderIntent, record: dict, status: str,
                  armed: limits.ArmingState) -> None:
        """Ledger a non-placement decision (measurement, not trading)."""
        row = _decision_row(intent, record, status, armed.live)
        try:
            self.ledger.append(row)
        except Exception:
            log.exception("skip-row append failed")

    def _confirm_releases(self) -> None:
        """Fire queued re-entry releases ONLY for orders the fills sync has
        proven terminal-with-zero-fill (qwen review f2): order in
        _fill_checked with no fill row, key untainted by any fill."""
        for oid in list(self._pending_release):
            key = self._pending_release[oid]
            if oid in self._filled_orders or key in self._filled_keys:
                del self._pending_release[oid]  # filled — never release
                continue
            if oid in self._fill_checked:
                del self._pending_release[oid]
                if key in self.seen:
                    self.seen.discard(key)
                    log.info("re-entry slot released for %s "
                             "(venue-confirmed unfilled cancel)", key)

    def sweep_stale(self, now_mono: float) -> int:
        """M5: cancel this session's maker orders older than MAKER_TTL_S.

        "cancelled" writes a clean cancel row (releases the exposure slot);
        "gone" writes gone_before_cancel (likely a fill — slot stays held
        until settle/reconcile); a failed cancel stays tracked, logs a
        MANUAL ACTION line, and is retried every sweep.
        """
        n = 0
        for order_id in list(self.resting):
            info = self.resting[order_id]
            # An ORDER the fills sync has proven FILLED needs no cancel:
            # hammering cancel on a terminal order spams the venue forever
            # (glm review f10). ORDER-scoped on purpose — key-scoped skipping
            # abandoned an unfilled re-entry sibling (qwen review f1).
            if order_id in self._filled_orders:
                self.ledger.append({
                    "type": "cancel", "order_id": order_id,
                    "dedup_key": info["dedup_key"], "market_slug": info["slug"],
                    "status": "gone_before_cancel", "source": "known_filled",
                    "age_s": round(now_mono - info["placed_mono"], 1),
                    "ts_ns": time.time_ns(),
                })
                del self.resting[order_id]
                n += 1
                continue
            if now_mono - info["placed_mono"] < MAKER_TTL_S:
                continue
            outcome = pmus.cancel(order_id, info["slug"])
            if outcome == "error:cancel_failed":
                log.error(
                    "MANUAL ACTION REQUIRED: cancel failed for order %s on %s "
                    "(dedup %s) — order may still be resting; will retry next "
                    "sweep; hand-cancel in the Polymarket US app if it persists.",
                    order_id, info["slug"], info["dedup_key"],
                )
                continue
            status = "cancelled" if outcome == "cancelled" else "gone_before_cancel"
            self.ledger.append(
                {
                    "type": "cancel",
                    "order_id": order_id,
                    "dedup_key": info["dedup_key"],
                    "market_slug": info["slug"],
                    "status": status,
                    "age_s": round(now_mono - info["placed_mono"], 1),
                    "ts_ns": time.time_ns(),
                }
            )
            # H-MAPENTRY1-REENTRY1: the frozen text releases on a cancel the
            # "venue confirmed was never filled" — cancel SUCCESS is not that
            # confirmation (the venue reports success on filled orders, seen
            # in production). Queue the release; it fires only after the
            # fills sync retrieves the order terminal-with-zero-fill
            # (qwen review f2).
            key = info["dedup_key"]
            if outcome == "cancelled" and key and key in self.seen:
                self._pending_release[order_id] = key
            if outcome == "gone":
                log.warning(
                    "order %s was gone before cancel (likely FILLED) on %s — "
                    "exposure slot stays held pending settle/reconcile",
                    order_id, info["slug"],
                )
            del self.resting[order_id]
            n += 1
        return n

    def cycle(self) -> int:
        # TTL sweep FIRST and whenever resting orders exist (not only while
        # armed): cancelling stale quotes is the safe direction, and nothing
        # earlier in the cycle may starve it — a deterministic raise in the
        # gate or the follow-tail must not leave live orders unswept (peer
        # review 08-26, finding 4).
        if self.resting:
            try:
                self.sweep_stale(time.monotonic())
            except Exception:
                log.exception("TTL sweep failed (retried next cycle)")
        try:
            armed = limits.check_armed()
        except Exception:
            log.exception("arming gate raised; failing closed to dry_run")
            armed = limits.ArmingState(
                dry_run=True, reasons=["arming gate exception (fail closed)"]
            )
        try:
            records = ledger_mod.load_records(self.ledger.root)
            self._daily_loss = ledger_mod.daily_loss_usd(
                records, f"{datetime.now(UTC):%Y%m%d}"
            )
            self.open_count = ledger_mod.open_exposure(records)
            self._refresh_ledger_state(records)
        except Exception:
            log.exception("ledger read failed; keeping previous caps state")
        n = 0
        for follow in (self.follow, self.follow_us):
            try:
                for record in follow.lines():
                    try:
                        if self.handle(record, armed):
                            n += 1
                    except Exception:
                        log.exception("bad shadow record skipped")
            except Exception:
                log.exception("shadow follow failed; continuing")
        self._cycles += 1
        # Venue truth (reconcile + fills/settles + adoption) runs whenever a
        # client exists — a mid-run DISARM must not stop truth ingestion for
        # positions already open (qwen review f4/f5). A never-armed process
        # has no client and correctly skips.
        if (armed.live or pmus._client is not None) and (
            self._cycles % RECONCILE_EVERY == 0
        ):
            self._venue_truth_pass()
        return n

    def _venue_truth_pass(self) -> None:
        """Reconcile ledger vs venue, adopt live orders, ingest fills/settles.

        Never raises into the cycle: a venue hiccup must not cost the
        engine its next TTL sweep.
        """
        try:
            records = ledger_mod.load_records(self.ledger.root)
            report = reconcile.run(
                records, pmus._get_client(), last=self._last_reconcile
            )
            # Append on ANOMALY-set change only — ledger_open/venue_open
            # counts drift every pass during a live session and would
            # re-append near-identical rows (peer review 08-26, finding 5).
            keys = ("gone_from_venue", "unknown_on_venue", "partial_fills", "error")
            if any(
                report.get(k) != (self._last_reconcile or {}).get(k) for k in keys
            ):
                self.ledger.append(
                    {"type": "reconcile", **report, "ts_ns": time.time_ns()}
                )
            self._last_reconcile = report
            self._adopt_venue_orders(report)
            for row in fills_mod.sync(
                records, pmus._get_client(), self._fill_checked, time.time_ns()
            ):
                self.ledger.append(row)
                if row.get("type") == "fill" and row.get("order_id"):
                    self._filled_orders.add(str(row["order_id"]))
                    if isinstance(row.get("dedup_key"), str):
                        self._filled_keys.add(row["dedup_key"])
            self._confirm_releases()
        except Exception:
            log.exception("reconcile pass failed (engine continues)")

    def _adopt_venue_orders(self, report: dict) -> None:
        """ADOPTION (glm review f1/f3): every live venue order must be under
        THIS process's TTL sweep. Prior-session ledger orders and unknown
        orphans (create errors that were really acks; single-account rail
        says nothing else trades here) both get adopted and cancelled by the
        normal sweep when stale.
        """
        for o in (report.get("open_orders") or []) + (
            report.get("unknown_orders") or []
        ):
            oid = str(o.get("id"))
            if oid and o.get("slug") and oid not in self.resting:
                # Backdated past TTL: an adopted order is by definition
                # already stale (unmanaged >=60s) — the next sweep cancels
                # it immediately instead of granting a fresh 150s of
                # adverse selection (qwen review f7).
                self.resting[oid] = {
                    "slug": o["slug"],
                    "dedup_key": o.get("dedup_key"),
                    "placed_mono": time.monotonic() - MAKER_TTL_S - 1,
                }
                log.warning(
                    "adopted venue order %s on %s into TTL sweep "
                    "(%s; cancel next sweep)", oid, o["slug"],
                    "prior session" if o.get("dedup_key") else "orphan",
                )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="single 30s pass then exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # The engine is the process that holds trading keys: the redaction
    # filter must be live HERE, not only in the capture supervisor
    # (security review 08-26, LOW-1 — config registers secrets, but
    # registration is inert until the filter is installed).
    from gridbot.redaction import install_log_redaction

    install_log_redaction()

    armed = limits.check_armed()
    mode = "LIVE (user-armed)" if armed.live else "dry_run"
    log.info("executor starting: %s; stake cap $%.2f; max open %d",
             mode, limits.max_stake_usd(), limits.max_open_positions())
    for reason in armed.reasons:
        log.info("  dry_run because: %s", reason)

    engine = Engine()
    deadline = time.monotonic() + 30 if args.once else None
    while True:
        try:
            engine.cycle()
        except Exception:
            # An armed session may hold resting orders; nothing transient
            # (keyring D-Bus outage, wire hiccup, bad ledger line) may kill
            # the process, or the TTL sweep dies with it.
            log.exception("cycle failed; engine continues")
        if deadline and time.monotonic() > deadline:
            break
        time.sleep(CYCLE_S)


if __name__ == "__main__":
    main()
