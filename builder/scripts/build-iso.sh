#!/bin/bash
# Build the Spiral live ISO with Debian live-build.
# Run on Debian trixie (the CI container) as root.
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
VERSION=$(tr -d '[:space:]' < "$ROOT/VERSION")
WORK="${SPIRAL_BUILD_DIR:-$ROOT/build/iso}"
OUTPUT="$ROOT/dist"
RELEASE_FILE=""
KEEP_WORK=0

usage() {
    echo "usage: build-iso.sh [--output DIR] [--release FILE] [--keep-work]" >&2
    exit 2
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --output) OUTPUT=$2; shift 2 ;;
        --release) RELEASE_FILE=$2; shift 2 ;;
        --keep-work) KEEP_WORK=1; shift ;;
        *) usage ;;
    esac
done

case "$WORK" in
    "$ROOT"/build/*) ;;
    *) echo "SPIRAL_BUILD_DIR must be under $ROOT/build" >&2; exit 2 ;;
esac

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

for program in lb debootstrap xorriso unsquashfs gpgv python3; do
    command -v "$program" >/dev/null 2>&1 || {
        echo "missing build dependency: $program (run builder/scripts/install-build-deps.sh)" >&2
        exit 2
    }
done

rm -rf "$WORK"
mkdir -p "$WORK" "$OUTPUT"

if [ -z "$RELEASE_FILE" ]; then
    RELEASE_FILE="$WORK/release.json"
    python3 "$ROOT/builder/scripts/discover-debian.py" --output "$RELEASE_FILE"
fi
[ -f "$RELEASE_FILE" ] || { echo "release metadata missing: $RELEASE_FILE" >&2; exit 2; }

CODENAME=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["codename"])' "$RELEASE_FILE")
DEBIAN_VERSION=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$RELEASE_FILE")

MIRROR=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["mirrors"]["debian"])' "$ROOT/config/debian.json")
SECURITY=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["mirrors"]["security"])' "$ROOT/config/debian.json")
ARCH=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["architecture"])' "$ROOT/config/debian.json")
AREAS=$(python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))["archive_areas"]))' "$ROOT/config/debian.json")
ISO_VOLUME=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["iso"]["volume_id"])' "$ROOT/config/debian.json")
ISO_APP=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["iso"]["application_id"])' "$ROOT/config/debian.json")
ISO_PUBLISHER=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["iso"]["publisher"])' "$ROOT/config/debian.json")

git config --global --add safe.directory "$ROOT" 2>/dev/null || true
GIT_SHA=$(git -C "$ROOT" rev-parse --short=7 HEAD 2>/dev/null || echo nogit)
GIT_DIRTY=false
if [ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null || true)" ]; then GIT_DIRTY=true; fi
BUILD_DATE=$(date -u +%Y%m%d)
BUILD_ID="${VERSION}-${CODENAME}-${BUILD_DATE}-${GIT_SHA}"
TAG="spiral-${BUILD_ID}"
export SPIRAL_GIT_COMMIT="$GIT_SHA"
export SPIRAL_GIT_DIRTY="$GIT_DIRTY"
export SPIRAL_LIVE_BUILD_VERSION
SPIRAL_LIVE_BUILD_VERSION=$(lb --version 2>/dev/null | head -n 1 || true)
export SPIRAL_LIVE_BUILD_VERSION

if [ -z "${SOURCE_DATE_EPOCH:-}" ]; then
    SOURCE_DATE_EPOCH=$(git -C "$ROOT" log -1 --format=%ct 2>/dev/null || date +%s)
fi
export SOURCE_DATE_EPOCH

CONFIG="$WORK/config"
INCLUDES="$CONFIG/includes.chroot"
mkdir -p "$CONFIG" "$INCLUDES/usr/lib/spiral-installer" "$INCLUDES/usr/share/spiral" "$INCLUDES/usr/bin"
cp -a "$ROOT/builder/live/config/." "$CONFIG/"
cp -a "$ROOT/installer/spiral_installer" "$INCLUDES/usr/lib/spiral-installer/"
cp -a "$ROOT/config" "$ROOT/profiles" "$INCLUDES/usr/share/spiral/"
cp -a "$ROOT/assets/branding" "$INCLUDES/usr/share/spiral/branding"
cp "$ROOT/VERSION" "$INCLUDES/usr/share/spiral/VERSION"
install -d "$INCLUDES/usr/share/spiral/test"
install -m 0644 "$ROOT/builder/live/test/choices-ext4.json" "$INCLUDES/usr/share/spiral/test/"
install -m 0644 "$ROOT/builder/live/test/choices-btrfs.json" "$INCLUDES/usr/share/spiral/test/"
install -m 0600 "$ROOT/builder/live/test/password.txt" "$INCLUDES/usr/share/spiral/test/password.txt"
install -m 0755 "$ROOT/builder/live/spiral-installer" "$INCLUDES/usr/bin/spiral-installer"
install -m 0755 "$ROOT/builder/live/spiral-installer-gui" "$INCLUDES/usr/bin/spiral-installer-gui"
install -m 0755 "$ROOT/builder/live/config/hooks/live/0100-spiral.hook.chroot" \
    "$CONFIG/hooks/live/0100-spiral.hook.chroot"

python3 "$ROOT/builder/render_config.py" \
    --root "$ROOT" \
    --config-dir "$CONFIG" \
    --release "$RELEASE_FILE" \
    --version "$VERSION" \
    --bootappend-file "$WORK/bootappend.txt"
BOOTAPPEND=$(cat "$WORK/bootappend.txt")

cd "$WORK"
$SUDO lb config noauto \
    --mode debian \
    --system live \
    --architecture "$ARCH" \
    --distribution "$CODENAME" \
    --archive-areas "$AREAS" \
    --mirror-bootstrap "$MIRROR" \
    --mirror-chroot "$MIRROR" \
    --mirror-chroot-security "$SECURITY" \
    --mirror-binary "$MIRROR" \
    --mirror-binary-security "$SECURITY" \
    --binary-images iso-hybrid \
    --bootloaders "grub-pc grub-efi" \
    --uefi-secure-boot auto \
    --bootappend-live "$BOOTAPPEND" \
    --iso-application "$ISO_APP" \
    --iso-publisher "$ISO_PUBLISHER" \
    --iso-volume "$ISO_VOLUME" \
    --apt-source-archives false \
    --checksums sha256 \
    --chroot-squashfs-compression-type zstd \
    --chroot-squashfs-compression-level 3 \
    --debian-installer none \
    --firmware-binary true \
    --firmware-chroot true \
    --memtest memtest86+

if [ -n "$SUDO" ]; then
    $SUDO -E lb build 2>&1 | tee "$WORK/build.log"
else
    lb build 2>&1 | tee "$WORK/build.log"
fi

RAW_ISO=$(find "$WORK" -maxdepth 1 -type f -name '*.iso' | head -n 1)
if [ -z "$RAW_ISO" ]; then
    echo "lb build did not produce an ISO" >&2
    exit 1
fi

ISO_NAME="Spiral-${BUILD_ID}-${ARCH}.iso"
ISO="$OUTPUT/$ISO_NAME"
cp "$RAW_ISO" "$ISO"

STATUS="$WORK/chroot/var/lib/dpkg/status"
if [ ! -f "$STATUS" ]; then
    echo "dpkg status database is missing; cannot create a package manifest" >&2
    exit 1
fi
python3 "$ROOT/builder/scripts/package_manifest.py" \
    --status "$STATUS" \
    --output "$OUTPUT/package-manifest.txt"

python3 "$ROOT/builder/scripts/create_manifest.py" \
    --iso "$ISO" \
    --release "$RELEASE_FILE" \
    --build-info "$INCLUDES/usr/share/spiral/build-info.json" \
    --package-manifest "$OUTPUT/package-manifest.txt" \
    --output "$OUTPUT/build-manifest.json"

(
    cd "$OUTPUT"
    sha256sum "$ISO_NAME" > SHA256SUMS
    sha256sum -c SHA256SUMS
)

printf '%s\n' "$TAG" > "$OUTPUT/build-id.txt"
cat > "$OUTPUT/release-notes.md" <<EOF
Spiral ${VERSION} is built from Debian ${DEBIAN_VERSION} (${CODENAME}) for ${ARCH}.

The ISO contains the Spiral live environment and installer. All packages come
from authenticated Debian repositories; the Debian Stable identity was read
from signed InRelease metadata during the build.

Verification:

    sha256sum -c SHA256SUMS

This build has passed structural ISO validation and unit tests in GitHub
Actions. Hardware-specific drivers, firmware and graphical sessions require
physical or virtual hardware testing that is tracked in docs/TESTING.md.
EOF

bash "$ROOT/builder/scripts/validate-iso.sh" \
    --iso "$ISO" \
    --release "$RELEASE_FILE" \
    --sha256 "$OUTPUT/SHA256SUMS"

if [ "$KEEP_WORK" -ne 1 ]; then
    echo "build workspace retained at $WORK for logs; remove it to reclaim disk"
fi
echo "ISO: $ISO"
echo "SHA256SUMS: $OUTPUT/SHA256SUMS"
echo "Tag: $TAG"
