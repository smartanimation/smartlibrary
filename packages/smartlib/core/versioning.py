from __future__ import annotations

import re
from pathlib import Path

VERSION_RE = re.compile(r"v(?P<version>\d{3,})", re.IGNORECASE)
TAKE_RE = re.compile(r"_(?P<take>\d{2,})(?=\.[^.]+$)")


def format_version(version: int) -> str:
    if version < 1:
        raise ValueError("version must be greater than zero")
    return f"v{version:03d}"


def parse_version(value: str | Path) -> int | None:
    match = VERSION_RE.search(str(value))
    return int(match.group("version")) if match else None


def parse_take(value: str | Path) -> int | None:
    match = TAKE_RE.search(Path(value).name)
    return int(match.group("take")) if match else None


def next_version(existing: list[int] | tuple[int, ...]) -> int:
    return (max(existing) + 1) if existing else 1


def next_take(existing: list[int] | tuple[int, ...]) -> int:
    return (max(existing) + 1) if existing else 1
