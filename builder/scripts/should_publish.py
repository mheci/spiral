#!/usr/bin/env python3
"""Decide whether a new build differs from the previous published build."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def comparable(manifest: dict) -> dict:
    return {
        "codename": manifest.get("debian", {}).get("codename"),
        "debian_version": manifest.get("debian", {}).get("version"),
        "package_manifest": manifest.get("package_manifest", {}).get("sha256"),
        "git_commit": manifest.get("git_commit"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", required=True)
    parser.add_argument("--previous")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    current = json.loads(Path(args.current).read_text(encoding="utf-8"))
    if args.force or not args.previous or not Path(args.previous).is_file():
        print("publish: no previous build or force requested")
        return 0
    previous = json.loads(Path(args.previous).read_text(encoding="utf-8"))
    now = comparable(current)
    before = comparable(previous)
    if now == before:
        print("skip: Debian base, package manifest and git commit are unchanged")
        return 10
    for key in now:
        if now[key] != before.get(key):
            print(f"publish: {key} changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
