"""Block commits that contain a real-looking Anthropic API key.

Real keys are long (sk-ant-api03- followed by ~90 characters). Requiring 32+ characters
after the prefix catches real keys while letting short, obviously fake test values through.

Usage (from the git pre-commit hook):
    python -m decline_desk.secret_scan <file> [<file> ...]
"""

import re
import sys
from pathlib import Path

KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_-]{32,}")


def find_secrets(text: str) -> list[int]:
    """Return the 1-based line numbers that contain a real-looking key."""
    return [n for n, line in enumerate(text.splitlines(), start=1) if KEY_PATTERN.search(line)]


def scan_files(paths: list[Path]) -> list[tuple[Path, int]]:
    hits: list[tuple[Path, int]] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (FileNotFoundError, UnicodeDecodeError):
            continue  # deleted in this commit, or binary
        hits.extend((path, line) for line in find_secrets(text))
    return hits


def main(argv: list[str]) -> int:
    hits = scan_files([Path(a) for a in argv])
    for path, line in hits:
        print(f"possible API key: {path}:{line}", file=sys.stderr)
    if hits:
        print("Commit blocked. Move the key to .env (gitignored).", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
