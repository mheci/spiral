#!/usr/bin/env python3
"""Check every package name used by Spiral against the Debian Stable archive."""

from __future__ import annotations

import argparse
import json
import lzma
import re
import sys
import urllib.request
from pathlib import Path

CODENAME_RE = re.compile(r"^[a-z][a-z0-9]*$")


def fetch(url: str, limit: int = 200 * 1024 * 1024) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Spiral-package-check/0.1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} for {url}")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError(f"response too large: {url}")
    return data


def stable_codename(suite: str) -> str:
    text = fetch(f"https://deb.debian.org/debian/dists/{suite}/InRelease", 2 * 1024 * 1024).decode(
        "utf-8", errors="replace"
    )
    match = re.search(r"^Codename:\s*(\S+)$", text, re.MULTILINE)
    if not match or not CODENAME_RE.match(match.group(1)):
        raise RuntimeError("could not read the Debian codename from InRelease")
    return match.group(1)


def package_names(component: str, codename: str, architecture: str) -> set[str]:
    url = f"https://deb.debian.org/debian/dists/{codename}/{component}/binary-{architecture}/Packages.xz"
    payload = fetch(url)
    try:
        text = lzma.decompress(payload).decode("utf-8", errors="replace")
    except lzma.LZMAError as exc:
        raise RuntimeError(f"invalid Packages.xz for {component}: {exc}") from exc
    names = set()
    for line in text.splitlines():
        if line.startswith("Package:"):
            names.add(line.split(":", 1)[1].strip())
    return names


def read_live_list(path: Path) -> list[str]:
    packages = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        packages.append(line)
    return packages


def expand(packages, suffix: str) -> set[str]:
    return {package.replace("{arch}", suffix) for package in packages}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--suite", default="stable")
    parser.add_argument("--architecture", default="amd64")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    sys.path.insert(0, str(root / "installer"))
    from spiral_installer.config import load_repository

    repo = load_repository(root)
    codename = stable_codename(args.suite)
    available: set[str] = set()
    for component in repo.debian.archive_areas:
        names = package_names(component, codename, args.architecture)
        print(f"{component}: {len(names)} packages")
        available |= names

    live_list = read_live_list(root / "builder" / "live" / "config" / "package-lists" / "spiral.list.chroot")
    hard: set[str] = set(live_list)
    for group_id in repo.installer.default_groups:
        hard |= expand(repo.profiles.package_group(group_id).packages, args.architecture)
    for desktop in repo.profiles.desktops.values():
        if desktop.status == "stable":
            hard |= expand(desktop.packages, args.architecture)
            if desktop.display_manager:
                hard.add(desktop.display_manager)
    for kernel in repo.profiles.kernels.values():
        hard |= expand(kernel.packages, args.architecture)
    for driver in repo.profiles.drivers.values():
        if driver.status == "stable":
            hard |= expand(driver.packages, args.architecture)
    for filesystem in repo.profiles.filesystems.values():
        hard |= set(filesystem.tools)

    optional: set[str] = set()
    for group in repo.profiles.packages.values():
        optional |= expand(group.packages, args.architecture)
    for desktop in repo.profiles.desktops.values():
        if desktop.status != "stable":
            optional |= expand(desktop.packages, args.architecture)
    for driver in repo.profiles.drivers.values():
        if driver.status != "stable":
            optional |= expand(driver.packages, args.architecture)

    missing_hard = sorted(hard - available)
    missing_optional = sorted(optional - available - hard)
    if missing_optional:
        print("optional packages not present on this release (will be skipped):")
        for name in missing_optional:
            print(f"  {name}")
    if missing_hard:
        print("REQUIRED packages are missing on Debian Stable:", file=sys.stderr)
        for name in missing_hard:
            print(f"  {name}", file=sys.stderr)
        return 1
    print(f"all {len(hard)} required package names exist on Debian {codename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
