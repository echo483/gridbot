# Security — secret handling & threat model

Same posture as cs2agent, minus its sprawl. The guarantee: **no secret value is
ever written to disk in this repo, printed, logged, committed, or shown to an LLM.**

## Storage

- **OS keyring only**, service `gridbot` (SecretService/KWallet). Written by
  `scripts/setup_secrets.py` via hidden `getpass` input; the script refuses
  non-encrypted keyring backends.
- **No secrets in files.** `.env.example` documents non-secret runtime knobs only.
  Env vars of a secret's name override the keyring solely for tests/CI fakes.
- Daemons run as the login user anchored to `graphical-session.target` so the
  keyring is actually unlockable (see `deploy/gridbot-capture.service` header).

## Runtime

- `gridbot.redaction.install_log_redaction()` masks every registered secret in
  every log record; `config` registers each secret on read.
- GRID auth is `x-api-key` **header** over HTTPS only — never a URL param
  (`graphql.post` refuses non-HTTPS). Errors are secret-free by construction.
- **LLMs never see keys.** Hermes/Claude agents work on data files and summaries;
  the capture and (future) execution paths read secrets directly from keyring.

## Repo hygiene

- `.githooks/pre-commit` runs `scripts/check_no_secrets.py` over staged changes
  (enable once: `git config core.hooksPath .githooks`).
- `.gitignore` excludes `.env*`, `data/`, key material patterns.
- Private repo (`echo483/gridbot`). Do not open public issues for suspected
  leaks; rotate the credential first, then document.

## Trading surface (P2+, not yet built)

- Executor ships `dry_run=True`; arming is the user's own manual act.
- Polymarket signing key enters the keyring only at P2, after an instrument
  passes and eligibility is confirmed. Per-trade/daily caps + kill switch are
  code-enforced and their changes require the user's sign-off.
