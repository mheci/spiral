#!/usr/bin/env python3
"""Create the release build manifest next to the ISO."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iso", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--build-info", required=True)
    parser.add_argument("--package-manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    iso = Path(args.iso)
    release = json.loads(Path(args.release).read_text(encoding="utf-8"))
    build_info = json.loads(Path(args.build_info).read_text(encoding="utf-8"))
    packages = Path(args.package_manifest)
    manifest = {
        "schema_version": 1,
        "name": build_info["name"],
        "spiral_version": build_info["spiral_version"],
        "debian": release,
        "architecture": build_info["architecture"],
        "iso": {
            "filename": iso.name,
            "size_bytes": iso.stat().st_size,
            "sha256": sha256(iso),
        },
        "package_manifest": {
            "filename": packages.name,
            "sha256": sha256(packages),
            "count": sum(1 for line in packages.read_text(encoding="utf-8").splitlines() if line),
        },
        "git_commit": build_info.get("git_commit", ""),
        "git_dirty": build_info.get("git_dirty", False),
        "source_date_epoch": build_info.get("source_date_epoch", ""),
        "built_at": build_info.get("built_at", ""),
        "manifest_created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "live_build_version": build_info.get("live_build_version", ""),
        "offline_enabled": build_info.get("offline_enabled", False),
    }
    Path(args.output).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"ISO SHA256 {manifest['iso']['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
