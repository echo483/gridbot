"""Data-driven engine replay: real capture through the real Engine.

Feeds an actual mapentry day file through Engine.handle with a FAKE wire
(no network, no keys) in simulated-armed mode, TTL-expiring every order
so the re-entry path exercises constantly, then asserts the frozen
policy invariants over the resulting ledger. This is the empirical leg
of the pre-arming verification: the invariants must hold on the real
row stream, not just on synthetic unit-test rows.

Run: uv run python scripts/replay_harness.py data/shadow/mapentry-YYYYMMDD.jsonl
Exit 0 = every invariant holds; non-zero prints violations.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    src_path = Path(sys.argv[1])
    rows = []
    for line in src_path.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict):
            rows.append(r)
    print(f"replaying {len(rows)} real rows from {src_path.name}")

    tmp = Path(tempfile.mkdtemp(prefix="gridbot-replay-"))
    (tmp / "shadow").mkdir()

    from gridbot import config
    config.DATA_DIR = tmp  # isolated ledger; never touches real data/

    from gridbot.executor import engine as engine_mod
    from gridbot.executor import ledger as ledger_mod
    from gridbot.executor import limits, pmus
    from gridbot.executor.intents import US_BAND_GATE

    # Assert the LIVE band — a hardcoded copy here drifted silently when the
    # band was widened to (0.40, 0.90) and flagged legal placements.
    BAND_LO, BAND_HI = US_BAND_GATE

    oid_counter = [0]

    def fake_place(_intent):
        oid_counter[0] += 1
        return "placed", f"SIM{oid_counter[0]:06d}"

    def fake_cancel(_oid, _slug):
        return "cancelled"  # venue-confirmed unfilled — maximally exercises re-entry

    pmus.place = fake_place
    pmus.cancel = fake_cancel

    eng = engine_mod.Engine(ledger=engine_mod.IntentLedger(tmp))
    armed = limits.ArmingState(dry_run=False)

    errors = 0
    for i, r in enumerate(rows):
        try:
            eng.handle(r, armed)
        except Exception as exc:  # noqa: BLE001 — replay must report, not die
            errors += 1
            print(f"  handle() raised on real row {i}: {type(exc).__name__}: {exc}")
        # Expire everything every 40 rows so TTL cancels + re-entries churn.
        if i % 40 == 39 and eng.resting:
            for info in eng.resting.values():
                info["placed_mono"] = time.monotonic() - engine_mod.MAKER_TTL_S - 1
            eng.sweep_stale(time.monotonic())
            # emulate the fills sync confirming every cancel zero-filled
            eng._fill_checked.update(eng._pending_release.keys())
            eng._confirm_releases()
            # refresh ledger-derived state exactly as cycle() does
            records = ledger_mod.load_records(tmp / "executor")
            eng._refresh_ledger_state(records)
            eng.open_count = ledger_mod.open_exposure(records)
    # final sweep
    if eng.resting:
        for info in eng.resting.values():
            info["placed_mono"] = time.monotonic() - engine_mod.MAKER_TTL_S - 1
        eng.sweep_stale(time.monotonic())

    records = ledger_mod.load_records(tmp / "executor")
    placed = [r for r in records if r.get("type") == "order_intent"
              and r.get("status") == "placed"]
    gated = [r for r in records if r.get("status") == "skipped:band_gate"]
    cancels = [r for r in records if r.get("type") == "cancel"]

    violations: list[str] = []
    if errors:
        violations.append(f"{errors} handle() exceptions on real rows")

    per_key = Counter(r.get("dedup_key") for r in placed)
    over = {k: n for k, n in per_key.items() if n > 2}
    if over:
        violations.append(f"REENTRY 2-cap violated: {over}")

    for r in placed:
        p = r.get("p_map")
        if not (isinstance(p, (int, float)) and BAND_LO <= p <= BAND_HI):
            violations.append(f"band gate violated: placed with p_map={p} "
                              f"key={r.get('dedup_key')}")
    for r in gated:
        p = r.get("p_map")
        if isinstance(p, (int, float)) and BAND_LO <= p <= BAND_HI:
            violations.append(f"in-band row wrongly gated: p_map={p}")

    if len(cancels) != len(placed):
        violations.append(f"unmanaged orders: {len(placed)} placed vs "
                          f"{len(cancels)} cancels after full TTL expiry")
    if ledger_mod.open_exposure(records) != 0:
        violations.append("exposure nonzero after all clean cancels + no fills")

    n_reentries = sum(1 for n in per_key.values() if n == 2)
    print(f"placed {len(placed)} on {len(per_key)} keys | re-entries {n_reentries} "
          f"| band-gated {len(gated)} | cancels {len(cancels)}")
    if violations:
        print("\nINVARIANT VIOLATIONS:")
        for v in violations:
            print(" -", v)
        return 1
    print("ALL INVARIANTS HOLD on real capture: band gate, 2-cap, "
          "full order management, zero residual exposure, zero exceptions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
