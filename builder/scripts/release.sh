#!/bin/bash
# Publish validated release artifacts with gh. Never overwrites an existing release.
set -Eeuo pipefail

DIST="dist"
REPO=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --dist) DIST=$2; shift 2 ;;
        --repo) REPO=$2; shift 2 ;;
        *) echo "usage: release.sh --dist DIR [--repo OWNER/NAME]" >&2; exit 2 ;;
    esac
done
[ -d "$DIST" ] || { echo "dist directory not found: $DIST" >&2; exit 1; }
TAG=$(cat "$DIST/build-id.txt")
TITLE=$(python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); print("Spiral %s (%s %s)" % (m["spiral_version"], m["debian"]["codename"], m["built_at"][:10]))' "$DIST/build-manifest.json")
ISO=$(find "$DIST" -maxdepth 1 -name '*.iso' | head -n 1)
[ -n "$ISO" ] || { echo "no ISO in $DIST" >&2; exit 1; }

SIZE=$(stat -c %s "$ISO")
if [ "$SIZE" -ge $((2 * 1024 * 1024 * 1024)) ]; then
    echo "ISO is at least 2 GiB and cannot be uploaded as a GitHub release asset." >&2
    echo "Reduce the live image or publish this artifact through storage that supports it." >&2
    exit 1
fi

gh_args=(--repo "$REPO")
if [ -z "$REPO" ]; then gh_args=(); fi
if gh release view "$TAG" "${gh_args[@]}" >/dev/null 2>&1; then
    echo "release $TAG already exists; refusing to overwrite" >&2
    exit 1
fi

if [ -n "${SPIRAL_GPG_PRIVATE_KEY:-}" ]; then
    printf '%s' "$SPIRAL_GPG_PRIVATE_KEY" | gpg --batch --import
    if [ -n "${SPIRAL_GPG_PASSPHRASE:-}" ]; then
        gpg --batch --yes --pinentry-mode loopback --passphrase "$SPIRAL_GPG_PASSPHRASE" \
            --armor --detach-sign "$DIST/SHA256SUMS"
    else
        gpg --batch --yes --armor --detach-sign "$DIST/SHA256SUMS"
    fi
    echo "signed SHA256SUMS"
fi

gh release create "$TAG" "${gh_args[@]}" \
    --draft \
    --title "$TITLE" \
    --notes-file "$DIST/release-notes.md"
gh release upload "$TAG" "${gh_args[@]}" \
    "$ISO" \
    "$DIST/SHA256SUMS" \
    "$DIST/build-manifest.json" \
    "$DIST/package-manifest.txt"
if [ -f "$DIST/SHA256SUMS.asc" ]; then
    gh release upload "$TAG" "${gh_args[@]}" "$DIST/SHA256SUMS.asc"
fi
gh release edit "$TAG" "${gh_args[@]}" --draft=false --latest
echo "published $TAG"
