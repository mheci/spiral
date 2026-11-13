from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Iterable, Optional, Sequence

REDACTED = "***"
_SENSITIVE_KEYS = ("password", "passphrase", "secret", "token", "key")


class Secret:
    """Holds a secret in memory. Repr, str and JSON output never reveal it."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not value:
            raise ValueError("empty secret")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    def __str__(self) -> str:
        return REDACTED


def redact_fields(fields: dict) -> dict:
    clean = {}
    for key, value in fields.items():
        if any(marker in key.lower() for marker in _SENSITIVE_KEYS):
            clean[key] = REDACTED
        elif isinstance(value, Secret):
            clean[key] = REDACTED
        else:
            clean[key] = value
    return clean


def human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if value < 1024 or unit == "PiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def parse_kib(value: str) -> int:
    """Parse a /proc/meminfo value such as '16302124 kB' into MiB."""
    match = re.match(r"\s*(\d+)\s*kB", value)
    if not match:
        raise ValueError(f"not a kB value: {value!r}")
    return int(match.group(1)) // 1024


def partition_path(disk_path: str, number: int) -> str:
    """Return the kernel name for a partition, including NVMe/mmcblk p separators."""
    base = disk_path.rstrip("/")
    if re.search(r"\d$", base):
        return f"{base}p{number}"
    return f"{base}{number}"


def read_text(path: str | Path, default: Optional[str] = None) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return default


def which(program: str) -> Optional[str]:
    from shutil import which as _which

    return _which(program)


def run_capture(
    argv: Sequence[str],
    timeout: int = 15,
    check: bool = False,
    env: Optional[dict] = None,
) -> subprocess.CompletedProcess:
    """Run a read-only probe. Never uses a shell, so output cannot be executed."""
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
        env={**os.environ, **env} if env else None,
    )


def split_lines(text: str) -> list[str]:
    return [line.rstrip() for line in text.splitlines() if line.strip()]


def dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def dump_json(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"
