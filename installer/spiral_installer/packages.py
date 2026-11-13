from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional, Sequence


def parse_packages_index(text: str) -> dict[str, str]:
    packages: dict[str, str] = {}
    for stanza in re.split(r"\n\s*\n", text):
        name = None
        version = ""
        for line in stanza.splitlines():
            if line.startswith("Package:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("Version:"):
                version = line.split(":", 1)[1].strip()
        if name:
            packages[name] = version
    return packages


def parse_apt_policy(text: str) -> dict[str, Optional[str]]:
    result: dict[str, Optional[str]] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        if line and not line.startswith((" ", "\t")):
            if line.endswith(":") and " " not in line:
                current = line[:-1]
                if current not in result:
                    result[current] = None
            else:
                current = None
            continue
        if current and line.strip().startswith("Candidate:"):
            candidate = line.split(":", 1)[1].strip()
            result[current] = None if candidate.lower() in ("(none)", "none") else candidate
    return result


def missing_packages(packages: Sequence[str], available: dict[str, object]) -> list[str]:
    return sorted(
        {
            package
            for package in packages
            if package not in available or not available[package]
        }
    )


class AptPackageResolver:
    """Availability resolver backed by the live system's apt-cache."""

    def __init__(self, run: Callable[..., object]) -> None:
        self._run = run

    def missing(self, packages: Sequence[str]) -> list[str]:
        wanted = sorted(set(packages))
        if not wanted:
            return []
        result = self._run(["apt-cache", "policy", *wanted], timeout=60)
        if getattr(result, "returncode", 1) != 0:
            return wanted
        policy = parse_apt_policy(getattr(result, "stdout", "") or "")
        return missing_packages(wanted, policy)

    def refresh(self) -> None:
        result = self._run(
            ["apt-get", "update", "-o", "Acquire::Retries=3"],
            timeout=300,
        )
        if getattr(result, "returncode", 1) != 0:
            raise RuntimeError(
                "could not refresh Debian package metadata; check the network connection"
            )


def offline_index_path(repo_root: Path, codename: str, architecture: str) -> Path:
    return repo_root / "dists" / codename / "main" / f"binary-{architecture}" / "Packages"


class OfflinePackageResolver:
    def __init__(self, packages: dict[str, str]) -> None:
        self._packages = packages

    @classmethod
    def from_directory(cls, repo_root: Path, codename: str, architecture: str) -> "OfflinePackageResolver":
        path = offline_index_path(repo_root, codename, architecture)
        if not path.is_file():
            raise RuntimeError(f"offline package index is missing: {path}")
        return cls(parse_packages_index(path.read_text(encoding="utf-8", errors="replace")))

    def missing(self, packages: Sequence[str]) -> list[str]:
        return missing_packages(packages, self._packages)


def check_offline_repository(
    repo_root: Path,
    codename: str,
    architecture: str,
    packages: Sequence[str],
) -> list[str]:
    resolver = OfflinePackageResolver.from_directory(repo_root, codename, architecture)
    return resolver.missing(packages)
