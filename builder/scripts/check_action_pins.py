#!/usr/bin/env python3
"""Report pinned GitHub Actions that have a newer release tag."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

USES_RE = re.compile(r"uses:\s*([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)@([0-9a-f]{40})")


def api(url: str, token: str) -> dict:
    if not url.startswith("https://api.github.com/"):
        raise ValueError("unexpected API URL")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "Spiral-action-pin-check",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310 - fixed API URL
        return json.loads(response.read().decode("utf-8"))


def pinned_actions(text: str) -> dict[tuple[str, str], str]:
    return {
        (owner, repo): sha
        for owner, repo, sha in USES_RE.findall(text)
    }


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN is required", file=sys.stderr)
        return 2
    root = Path.cwd()
    pins: dict[tuple[str, str], str] = {}
    for path in sorted((root / ".github" / "workflows").glob("*.yml")):
        pins.update(pinned_actions(path.read_text(encoding="utf-8")))

    changes = []
    for (owner, repo), pinned in sorted(pins.items()):
        try:
            release = api(f"https://api.github.com/repos/{owner}/{repo}/releases/latest", token)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise
        tag = release.get("tag_name")
        if not tag:
            continue
        try:
            ref = api(f"https://api.github.com/repos/{owner}/{repo}/git/ref/tags/{tag}", token)
        except urllib.error.HTTPError:
            continue
        sha = ref.get("object", {}).get("sha")
        if sha and sha != pinned:
            changes.append((f"{owner}/{repo}", tag, pinned, sha))

    if not changes:
        print("all pinned actions match their latest release")
        return 0
    print("Pinned GitHub Actions with newer releases:\n")
    print("| Action | Latest tag | Pinned SHA | Latest SHA |")
    print("| --- | --- | --- | --- |")
    for name, tag, pinned, latest in changes:
        print(f"| {name} | {tag} | `{pinned}` | `{latest}` |")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
