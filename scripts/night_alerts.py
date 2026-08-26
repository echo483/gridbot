"""Telegram push alerts for the armed night session. Read-only tail of the
intents ledger; sends via `hermes send -t telegram`. Alerts on the rows that
matter (placed / likely-fill / cancel-failure / reconcile anomalies) and
stays silent on dry_run, band-gate skips, and clean TTL cancels."""

from __future__ import annotations

import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
POLL_S = 3.0
HEARTBEAT_S = 3600.0


def send(msg: str) -> None:
    subprocess.run(["hermes", "send", "-t", "telegram", msg[:900]],
                   capture_output=True, timeout=30)


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


def main() -> None:
    send("👁 gridbot night alerts armed — placed/fills/errors/reconcile will push here")
    offsets: dict[Path, int] = {}
    last_beat = time.monotonic()
    counts = {"placed": 0, "fills": 0}
    while True:
        day = f"{datetime.now(UTC):%Y%m%d}"
        path = DATA / "executor" / f"intents-{day}.jsonl"
        if path.exists():
            off = offsets.get(path, 0)
            with open(path) as fh:
                fh.seek(off)
                chunk = fh.read()
                offsets[path] = off + len(chunk)
            for line in chunk.splitlines():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                m = fmt(r)
                if m:
                    if "PLACED" in m:
                        counts["placed"] += 1
                    if "FILL" in m:
                        counts["fills"] += 1
                    send(m)
        if time.monotonic() - last_beat >= HEARTBEAT_S:
            last_beat = time.monotonic()
            send(f"🌙 gridbot heartbeat: session alive, {counts['placed']} placed / {counts['fills']} likely fills so far")
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
