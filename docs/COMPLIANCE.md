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
- **2026-08-25 — venue is Polymarket US (CFTC-regulated)**: the account is on
  Polymarket US (QCEX rails), KYC approved in the US app; API keys issued via
  polymarket.us/developer. The international CLOB is close-only for US IPs and
  is not used for orders. ELIGIBILITY CONFIRMED: YES (the arming gate requires
  this exact affirmation line).
- **Standing go-live gates (unchanged)**: `dry_run=True` until a pre-registered
  PASS; flipping live is the user's own act — never an automated process's;
  single account per platform; first live test sized at $1.

Open items:
- **Re-confirm eligibility for the operator's current location before the next
  armed session.** The 2026-08-24 confirmation above was given for California;
  the operator is now in Florida. The arming gate checks only for the
  affirmation line, so this has to be re-checked by hand.
- Venue keys enter the secrets file only (`SECURITY.md`), never the repo.
