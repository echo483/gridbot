"""Write gridbot secrets to the OS keyring via hidden input. Never echoes values.

Run: uv run python scripts/setup_secrets.py
"""

from __future__ import annotations

import getpass

import keyring

from gridbot.config import KEYRING_SERVICE

SECRETS = {
    "GRID_API_KEY": "GRID Open Access x-api-key",
    "SUPABASE_SERVICE_ROLE_KEY": "Supabase sb_secret write key (corpus mirror)",
    # P2 (live arming) only — leave blank to skip. Polymarket [ADDRESS] API keys
    # come from https://polymarket.us/developer (Ed25519; secret shown ONCE).
    "POLYMARKET_US_KEY_ID": "Polymarket US API Key ID (P2 only)",
    "POLYMARKET_US_SECRET_KEY": "Polymarket [ADDRESS] API Secret Key (P2 only; shown once at creation)",
    # Optional: HMAC secret for the health-escalation webhook. Only needed if
    # GRIDBOT_ESCALATE_URL is set; without it escalation is skipped.
    "GRIDBOT_ESCALATE_SECRET": "HMAC secret for the health escalation webhook",
}

ALLOWED_BACKENDS = ("SecretService", "Kwallet", "KWallet", "libsecret")


def assert_secure_backend() -> None:
    backend = type(keyring.get_keyring()).__module__ + "." + type(keyring.get_keyring()).__name__
    if not any(tag.lower() in backend.lower() for tag in ALLOWED_BACKENDS):
        raise SystemExit(
            f"Refusing to store secrets: keyring backend {backend!r} is not an encrypted "
            "OS keyring (SecretService/KWallet). Fix the keyring before continuing."
        )


def main() -> None:
    assert_secure_backend()
    for name, desc in SECRETS.items():
        existing = keyring.get_password(KEYRING_SERVICE, name)
        prompt = f"{name} ({desc}) [{'set — enter to keep' if existing else 'unset — enter to skip'}]: "
        value = getpass.getpass(prompt)
        if value:
            keyring.set_password(KEYRING_SERVICE, name, value)
            print(f"  stored {name}")
        else:
            print(f"  kept {name}" if existing else f"  skipped {name}")


if __name__ == "__main__":
    main()
