# Security — secret handling & threat model

The guarantee: **no secret value is committed, printed, logged, or handed to
anything that reasons.** It lives in one file on the host that runs the
system, and nowhere else.

## Storage — one file

- **Every secret is a line in one file**, `NAME=value`, read only by
  `gridbot.config`. Lookup order:
  1. `$GRIDBOT_SECRETS_FILE`
  2. `$CREDENTIALS_DIRECTORY/gridbot-secrets` — systemd `LoadCredential=`,
     the intended form on a server: systemd copies the file into a private,
     non-swappable directory visible only to the unit. *Verify on the host
     before relying on it (spec M1):* if systemd grants access through an ACL,
     the ACL mask shows as group bits and this loader refuses the file.
  3. `~/.config/gridbot/secrets.env`
- **The file must be owned by the service user (or root) and carry no group or
  other permission bits** (`0600` or `0400`). Symlinks, non-regular files,
  malformed lines, and loose permissions are refused, and every secret then
  reads as absent. A refusal is logged once, without values.
- **The environment is not a secret source.** Env vars leak to child
  processes and into `/proc/<pid>/environ`; nothing reads a secret from them.
- **The file is read in full on every lookup, with no cache.** Removing a key
  disarms a running engine on its next cycle, with no restart, even after a
  same-size in-place edit.
- Format: `NAME=value`, one per line, `#` comment lines. The value is
  everything after the first `=` (no inline comments). A repeated name, a
  malformed line, invalid UTF-8, or a file over 64 KiB refuses the whole file.
- Create it:

  ```
  install -d -m 700 ~/.config/gridbot
  install -m 600 /dev/null ~/.config/gridbot/secrets.env
  $EDITOR ~/.config/gridbot/secrets.env      # GRID_API_KEY=..., one per line
  uv run python -m gridbot.config --check    # presence + safety, never values
  ```

Known names: `GRID_API_KEY` (capture), `POLYMARKET_US_KEY_ID` and
`POLYMARKET_US_SECRET_KEY` (armed execution only), `GRIDBOT_ESCALATE_SECRET`
(optional webhook HMAC). The authoritative list is `config.SECRET_NAMES`.

**Why not the desktop keyring (the previous design).** Secret Service failed
both ways three times in nine days on the workstation: it crashed mid-call and
killed the armed engine, then came back locked with reads that blocked forever,
which took capture GRID-dark for 1.5–6 h at a time. A headless always-on host
has no unlocking login session, so the keyring was the wrong primitive there.
A permission-checked file has no daemon to fail. Its threat model is plain: an
attacker who can read the service user's files already owns the process. The
history is in `docs/analysis/2026-08-31-keyring-capture-outage.md`.

## Runtime

- `gridbot.redaction.install_log_redaction()` masks every registered secret in
  every log record; `config` registers each value when it loads the file.
- GRID auth is the `x-api-key` **header** over HTTPS only, never a URL param
  (`graphql.post` refuses non-HTTPS). Errors are secret-free by construction.
- **Nothing that reasons sees a key.** No model, report generator, or analysis
  script is given a secret. Only the capture and execution paths call
  `config.require`.

## Repo hygiene

- `.githooks/pre-commit` runs `scripts/check_no_secrets.py` over staged changes
  (enable once: `git config core.hooksPath .githooks`); CI runs the same scan.
- `.gitignore` excludes `.env`, `secrets.env`, `data/`.
- Private repo (`echo483/gridbot`). Do not open public issues for suspected
  leaks; rotate the credential first, then document.

## Trading surface

- The executor ships `dry_run=True`; arming is the operator's own manual act
  (`docs/LIVE_FIRE.md`), re-checked every engine cycle.
- Venue keys go into the secrets file only for an armed session. Per-trade and
  daily caps and the kill switch are enforced in code, and changing them
  requires the operator's sign-off.
