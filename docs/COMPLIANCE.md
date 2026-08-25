# Compliance ledger

Standing record of the eligibility and terms facts that gate live trading.
Every entry is dated; the go-live gates reference this file.

- **2026-08-24 — GRID data terms**: user registered for GRID Open Access under
  the **betting use case** (their own signup). Trading use of GRID OA data is
  therefore within the declared registration.
- **2026-08-24 — Polymarket eligibility (user confirmation, verbatim intent)**:
  user confirms prediction-market use is legal in California, is located in
  Los Angeles, and has previously used Polymarket personally on mobile. This is
  the standing eligibility confirmation required before the first live order.
- **Standing go-live gates (unchanged)**: `dry_run=True` until a pre-registered
  PASS; flipping live is the user's own act — never Claude's, never an agent's;
  single account per platform; first live test sized at $1.

Open items: Polymarket account credentials pending (user setting up desktop
access; keys enter the OS keyring via `scripts/setup_secrets.py` only).
