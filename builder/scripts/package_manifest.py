#!/usr/bin/env python3
"""Write a sorted package manifest from a Debian dpkg status database."""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_status(text: str) -> dict[str, str]:
    packages: dict[str, str] = {}
    for stanza in text.split("\n\n"):
        name = version = architecture = status = None
        for line in stanza.splitlines():
            if line.startswith("Package:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("Version:"):
                version = line.split(":", 1)[1].strip()
            elif line.startswith("Architecture:"):
                architecture = line.split(":", 1)[1].strip()
            elif line.startswith("Status:"):
                status = line.split(":", 1)[1].strip()
        if name and version and architecture and status == "install ok installed":
            packages[name] = f"{version} ({architecture})"
    return packages


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    status = Path(args.status)
    if not status.is_file():
        raise SystemExit(f"dpkg status database not found: {status}")
    packages = parse_status(status.read_text(encoding="utf-8", errors="replace"))
    lines = [f"{name}={version}" for name, version in sorted(packages.items())]
    Path(args.output).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(packages)} packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
