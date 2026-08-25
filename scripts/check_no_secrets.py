"""Pre-commit secret scanner: block key-shaped strings from entering the repo.

Scans STAGED changes. Lines carrying `pragma: allowlist secret` are permitted
(test fixtures with obviously-fake values only).
"""

from __future__ import annotations

import re
import subprocess
import sys

PATTERNS = [
    re.compile(r"sk-or-v1-[A-Za-z0-9]{16,}"),          # OpenRouter
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),        # GitHub tokens
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),      # Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                 # AWS
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b0x[a-fA-F0-9]{64}\b"),                # raw EVM private key
    re.compile(
        r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9+/_\-]{20,}['\"]"
    ),
]


def main() -> int:
    diff = subprocess.run(
        ["git", "diff", "--cached", "-U0", "--no-color"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    bad: list[str] = []
    path = "?"
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        if "pragma: allowlist secret" in line:
            continue
        for pat in PATTERNS:
            if pat.search(line):
                bad.append(f"{path}: {line[1:80]}…")
                break
    if bad:
        print("BLOCKED: staged changes look like they contain secrets:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        print("If a FAKE fixture: append `pragma: allowlist secret`.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
