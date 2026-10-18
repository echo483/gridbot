"""Push alerts for an armed night session. Read-only tail of the intents
ledger. Alerts on the rows that matter (placed / likely-fill / cancel-failure
/ reconcile anomalies) and stays silent on dry_run, band-gate skips, and clean
TTL cancels. Transport is configuration — see `gridbot.notify`."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from gridbot import notify

DATA = Path(__file__).resolve().parent.parent / "data"
POLL_S = 3.0
HEARTBEAT_S = 3600.0


def send(msg: str) -> None:
    """Deliver an alert. With no transport configured this prints locally."""
    notify.send(msg[:900], level="alert")


def alert_key(r: dict) -> str:
    """Stable identity so repeated ledger rows never re-alert (the reconcile
    pass re-flags the same cancelled order every ~60s — known engine bug,
    STATE queue 1b)."""
    t = r.get("type")
    if t == "reconcile":
        ids = sorted(set((r.get("gone_from_venue") or []) + (r.get("unknown_on_venue") or [])))
        return "recon:" + ",".join(ids)
    return f"{t}:{r.get('order_id')}:{r.get('status')}"


def fmt(r: dict) -> str | None:
    s = r.get("status", "")
    slug = str(r.get("market_slug", ""))[-30:]
    if r.get("type") == "order_intent":
        core = f"{slug} {r.get('us_side')} @{r.get('price')} x{r.get('shares')}"
        if s == "placed":
            return f"🟢 gridbot PLACED {core} oid={str(r.get('order_id'))[:10]}"
        if s.startswith("error"):
            return f"🚨 gridbot ORDER {s} {core}"
        return None
    if r.get("type") == "cancel":
        if s == "gone_before_cancel":
            return f"💰 gridbot LIKELY FILL {slug} (gone before TTL cancel)"
        if s.startswith("error"):
            return f"🚨 gridbot CANCEL FAILED {slug} oid={str(r.get('order_id'))[:10]} — check terminal"
        return None
    if r.get("type") == "reconcile":
        if r.get("gone_from_venue") or r.get("unknown_on_venue") or r.get("partial_fills"):
            return "🚨 gridbot RECONCILE anomaly: " + json.dumps(
                {k: r.get(k) for k in ("gone_from_venue", "unknown_on_venue", "partial_fills")})[:300]
        return None
    return None


def _drain(path: Path, offsets: dict[Path, int], seen: set[str],
           counts: dict[str, int]) -> None:
    """Alert every unseen row of ``path`` from its stored offset to EOF.

    Binary offsets: len() of a decoded str counts characters, not bytes —
    any multi-byte name would desync the seek position.
    """
    off = offsets.get(path, 0)
    with open(path, "rb") as fh:
        fh.seek(off)
        raw = fh.read()
        offsets[path] = off + len(raw)
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = fmt(r)
        if m:
            k = alert_key(r)
            if k in seen:
                continue
            seen.add(k)
            if "PLACED" in m:
                counts["placed"] += 1
            if "FILL" in m:
                counts["fills"] += 1
            send(m)


def main() -> None:
    send("👁 gridbot night alerts armed (dedup v3) — placed/fills/errors/reconcile push once each")
    offsets: dict[Path, int] = {}
    seen: set[str] = set()
    last_beat = time.monotonic()
    counts = {"placed": 0, "fills": 0}
    counts_day = f"{datetime.now(UTC):%Y%m%d}"
    first_scan = True
    while True:
        day = f"{datetime.now(UTC):%Y%m%d}"
        if day != counts_day:
            # The process is long-lived; without this the heartbeat counts
            # accumulate across nights (reported "18 placed" when the day's
            # ledger had 4 — nights 1+2 summed).
            counts_day = day
            counts = {"placed": 0, "fills": 0}
        path = DATA / "executor" / f"intents-{day}.jsonl"
        # UTC-rollover safety (audit F2): keep draining YESTERDAY's file
        # until today's exists — rows appended just before midnight must
        # still alert (PLACED / CANCEL FAILED land mid-armed-session at
        # 17:00 PT). The single-writer day-per-append invariant means an
        # old file stops growing once the new day file exists, so a final
        # drain then is complete and tracking can be dropped.
        for old in [p for p in offsets if p != path]:
            try:
                _drain(old, offsets, seen, counts)
            except OSError:
                pass
            if path.exists():
                offsets.pop(old, None)
        if path.exists():
            if path not in offsets:
                # Skip backlog only at PROCESS start; a file appearing while
                # we are already running is a day rollover and every row in
                # it is a new event (audit F2b: st_size here dropped all
                # rows written before our first post-rollover poll).
                offsets[path] = path.stat().st_size if first_scan else 0
                if first_scan:
                    first_scan = False
                    continue
            _drain(path, offsets, seen, counts)
        first_scan = False
        if time.monotonic() - last_beat >= HEARTBEAT_S:
            last_beat = time.monotonic()
            send(f"🌙 gridbot heartbeat: session alive, {counts['placed']} placed / {counts['fills']} likely fills so far")
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
