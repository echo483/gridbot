#!/usr/bin/env python3
"""
Preamble linter — catches drift, contradictions, bloat before they bite.
Runs on every preamble change; blocks PRs that fail.

Checks:
1. All referenced file paths exist
2. Token count < hard limit (default: 2000)
3. No obvious contradictions (static analysis)

Usage:
    uv run python scripts/preamble_lint.py agents/programming_preamble.txt
    uv run python scripts/preamble_lint.py --all
"""

import argparse
import re
import sys
from pathlib import Path

TOKEN_LIMIT = 2000
PREAMBLE_DIR = Path("agents")
PREAMBLE_PATTERNS = ["*programming*.md", "*review*.md", "*draft*.md", "*verify*.md", "*fast*.md", "*analysis*.md"]

# Contradiction patterns: pairs of directives that conflict
CONTRADICTIONS = [
    (r"never use tool", r"always use tool"),
    (r"do not modify", r"modify"),
    (r"read only", r"write"),
    (r"skip verification", r"verify"),
    (r"trust agent output", r"mechanically verify"),
]


def count_tokens(text: str) -> int:
    """Rough token count (words + punctuation)."""
    return len(re.findall(r"\w+|[^\w\s]", text))


def check_file_references(preamble_path: Path, preamble_text: str) -> list[str]:
    """Check that all referenced file paths exist."""
    errors = []
    # Extract paths that look like file references (heuristic)
    # Matches: /path/to/file.py, ./file.md, file.txt, path/file.yaml
    # Skips: model names (z-ai/glm-5.3), URLs (http), emails (@), pure identifiers
    path_pattern = r'["\']?([./][\w./-]+\.[\w]+|[\w-]+\.[\w]+)["\']?'
    for match in re.finditer(path_pattern, preamble_text):
        path_str = match.group(1)
        # Skip obvious non-paths
        if path_str.startswith("http") or "@" in path_str:
            continue
        if re.match(r"^[a-z0-9-]+/[a-z0-9.-]+$", path_str):  # model names like z-ai/glm-5.3
            continue
        if path_str.startswith("/"):  # absolute paths that aren't real files
            continue
        # Try to resolve relative to repo root
        candidate = Path(path_str)
        if not candidate.exists():
            # Try relative to preamble dir
            candidate = preamble_path.parent / path_str
            if not candidate.exists():
                errors.append(f"Referenced file not found: {path_str}")
    return errors


def check_contradictions(preamble_text: str) -> list[str]:
    """Check for obvious contradictory directives."""
    errors = []
    text_lower = preamble_text.lower()
    for pat1, pat2 in CONTRADICTIONS:
        if re.search(pat1, text_lower) and re.search(pat2, text_lower):
            errors.append(f"Potential contradiction: '{pat1}' and '{pat2}' both appear")
    return errors


def lint_preamble(path: Path, token_limit: int = TOKEN_LIMIT) -> tuple[bool, list[str]]:
    """Lint a single preamble file. Returns (passed, errors)."""
    errors = []

    if not path.exists():
        return False, [f"File not found: {path}"]

    text = path.read_text()

    # Check 1: token count
    tokens = count_tokens(text)
    if tokens > token_limit:
        errors.append(f"Token count {tokens} exceeds limit {token_limit}")

    # Check 2: file references
    ref_errors = check_file_references(path, text)
    errors.extend(ref_errors)

    # Check 3: contradictions
    contra_errors = check_contradictions(text)
    errors.extend(contra_errors)

    return len(errors) == 0, errors


def main():
    parser = argparse.ArgumentParser(description="Preamble linter")
    parser.add_argument("path", nargs="?", help="Path to preamble file")
    parser.add_argument("--all", action="store_true", help="Lint all preambles in agents/")
    parser.add_argument("--limit", type=int, default=TOKEN_LIMIT, help=f"Token limit (default: {TOKEN_LIMIT})")
    args = parser.parse_args()

    token_limit = args.limit

    if args.all:
        paths = []
        for pattern in PREAMBLE_PATTERNS:
            paths.extend(PREAMBLE_DIR.glob(pattern))
        paths = list(set(paths))  # dedupe
        if not paths:
            print(f"No preamble files found in {PREAMBLE_DIR}/ matching {PREAMBLE_PATTERNS}")
            sys.exit(1)
    elif args.path:
        paths = [Path(args.path)]
    else:
        print("Specify a path or --all")
        sys.exit(1)

    all_passed = True
    for path in paths:
        print(f"\nLinting: {path}")
        _passed, errors = lint_preamble(path, token_limit=token_limit)
        if errors:
            all_passed = False
            for err in errors:
                print(f"  ✗ {err}")
        else:
            print("  ✓ Passed")

    if not all_passed:
        print("\n❌ Some preambles failed linting")
        sys.exit(1)
    else:
        print("\n✅ All preambles passed")
        sys.exit(0)


if __name__ == "__main__":
    main()
