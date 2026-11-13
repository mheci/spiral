#!/usr/bin/env python3
"""Discover Debian Stable from signed InRelease metadata."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024
CODENAME_RE = re.compile(r"^[a-z][a-z0-9]*$")


def parse_inrelease(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith(" ") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    for required in ("Suite", "Codename", "Version"):
        if required not in fields:
            raise ValueError(f"InRelease is missing the {required} field")
    if not CODENAME_RE.match(fields["Codename"]):
        raise ValueError("InRelease contains an invalid Codename")
    return fields


def download(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Spiral-ISO-builder/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status != 200:
            raise ValueError(f"HTTP {response.status} for {url}")
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("InRelease is unexpectedly large")
    return payload.decode("utf-8", errors="strict")


def verify(path: Path, keyring: Path) -> None:
    result = subprocess.run(
        ["gpgv", "--keyring", str(keyring), str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"Debian InRelease signature verification failed:\n{result.stderr}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default="https://deb.debian.org/debian/dists/stable/InRelease",
    )
    parser.add_argument(
        "--keyring",
        default="/usr/share/keyrings/debian-archive-keyring.gpg",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--codename", help="skip download for a deterministic test fixture")
    args = parser.parse_args()

    if args.codename:
        if not CODENAME_RE.match(args.codename):
            raise SystemExit("invalid codename")
        data = {
            "suite": "stable",
            "codename": args.codename,
            "version": "test",
            "date": "",
            "source": "command line",
            "verified": False,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    else:
        keyring = Path(args.keyring)
        if not keyring.is_file():
            raise SystemExit(f"Debian keyring not found: {keyring}")
        text = download(args.url)
        with tempfile.NamedTemporaryFile("w", suffix="InRelease", delete=False, encoding="utf-8") as handle:
            handle.write(text)
            temp_path = Path(handle.name)
        try:
            verify(temp_path, keyring)
        finally:
            temp_path.unlink(missing_ok=True)
        fields = parse_inrelease(text)
        data = {
            "suite": fields["Suite"],
            "codename": fields["Codename"],
            "version": fields["Version"],
            "date": fields.get("Date", ""),
            "source": args.url,
            "verified": True,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Debian {data['version']} ({data['codename']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
