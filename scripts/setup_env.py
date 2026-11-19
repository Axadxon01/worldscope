#!/usr/bin/env python3
"""Create .env from .env.example with fresh random secrets.

Each "change-me" placeholder gets a random value. The PostgreSQL and Redis passwords
are reused inside DATABASE_URL and REDIS_URL so the file is consistent. An existing
.env is never overwritten.
"""

from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"


def render(template: str) -> str:
    postgres_password = secrets.token_hex(24)
    redis_password = secrets.token_hex(24)
    secret_key = secrets.token_hex(32)

    replacements = {
        "POSTGRES_PASSWORD": postgres_password,
        "REDIS_PASSWORD": redis_password,
        "SECRET_KEY": secret_key,
    }
    lines = []
    for line in template.splitlines():
        key = line.split("=", 1)[0]
        if key in replacements:
            line = f"{key}={replacements[key]}"
        elif key == "DATABASE_URL":
            line = re.sub(r":change-me@", f":{postgres_password}@", line)
        elif key == "REDIS_URL":
            line = re.sub(r":change-me@", f":{redis_password}@", line)
        lines.append(line)
    leftover = [ln for ln in lines if "change-me" in ln and not ln.lstrip().startswith("#")]
    if leftover:
        raise SystemExit(f"Unfilled placeholder in .env.example: {leftover[0].split('=')[0]}")
    return "\n".join(lines) + "\n"


def main() -> int:
    if TARGET.exists():
        print(".env already exists; leaving it unchanged.")
        return 0
    TARGET.write_text(render(EXAMPLE.read_text()))
    TARGET.chmod(0o600)
    print("Created .env with random secrets. Add ANTHROPIC_API_KEY to enable AI features.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
