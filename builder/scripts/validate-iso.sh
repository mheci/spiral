#!/bin/bash
# Structural validation of a built Spiral ISO.
set -Eeuo pipefail

ISO=""
RELEASE=""
SHA256=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --iso) ISO=$2; shift 2 ;;
        --release) RELEASE=$2; shift 2 ;;
        --sha256) SHA256=$2; shift 2 ;;
        *) echo "usage: validate-iso.sh --iso FILE --release FILE [--sha256 FILE]" >&2; exit 2 ;;
    esac
done
[ -f "$ISO" ] || { echo "ISO not found: $ISO" >&2; exit 1; }
[ -f "$RELEASE" ] || { echo "release metadata not found: $RELEASE" >&2; exit 1; }

for program in xorriso unsquashfs sha256sum; do
    command -v "$program" >/dev/null 2>&1 || { echo "missing validator: $program" >&2; exit 1; }
done

CODENAME=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["codename"])' "$RELEASE")
SIZE=$(stat -c %s "$ISO")
if [ "$SIZE" -lt $((512 * 1024 * 1024)) ]; then
    echo "ISO is smaller than 512 MiB: $SIZE bytes" >&2
    exit 1
fi

TMP=$(mktemp -d)
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

xorriso -indev "$ISO" -pvd_info > "$TMP/pvd.txt" 2>&1
grep -qi "Volume Id.*SPIRAL" "$TMP/pvd.txt" || {
    echo "ISO volume id is not SPIRAL" >&2
    cat "$TMP/pvd.txt" >&2
    exit 1
}

xorriso -indev "$ISO" -report_el_torito plain > "$TMP/eltorito.txt" 2>&1
grep -qi "El Torito boot img" "$TMP/eltorito.txt" || {
    echo "ISO has no El Torito boot image" >&2
    cat "$TMP/eltorito.txt" >&2
    exit 1
}
if ! grep -qiE "UEFI|EFI" "$TMP/eltorito.txt"; then
    echo "ISO has no UEFI boot entry" >&2
    cat "$TMP/eltorito.txt" >&2
    exit 1
fi

if ! xorriso -osirrox on -indev "$ISO" -extract /live "$TMP/live" >/dev/null 2>&1; then
    echo "could not extract /live from the ISO" >&2
    xorriso -indev "$ISO" -find /live -maxdepth 1 -print 2>&1 >&2 || true
    exit 1
fi
shopt -s nullglob
kernels=("$TMP/live"/vmlinuz*)
initrds=("$TMP/live"/initrd*)
if [ "${#kernels[@]}" -lt 1 ]; then
    echo "no kernel image under /live" >&2
    ls -la "$TMP/live" >&2
    exit 1
fi
if [ "${#initrds[@]}" -lt 1 ]; then
    echo "no initramfs under /live" >&2
    ls -la "$TMP/live" >&2
    exit 1
fi
[ -f "$TMP/live/filesystem.squashfs" ] || { echo "missing /live/filesystem.squashfs" >&2; exit 1; }

if ! RELEASE_IN_IMAGE=$(unsquashfs -cat "$TMP/live/filesystem.squashfs" usr/share/spiral/release.json 2>/dev/null); then
    RELEASE_IN_IMAGE=$(unsquashfs -cat "$TMP/live/filesystem.squashfs" /usr/share/spiral/release.json)
fi
printf '%s' "$RELEASE_IN_IMAGE" | python3 -c '
import json, sys
expected = sys.argv[1]
data = json.load(sys.stdin)
actual = data.get("codename")
if actual != expected:
    raise SystemExit("ISO codename %r does not match %r" % (actual, expected))
' "$CODENAME"

unsquashfs -l "$TMP/live/filesystem.squashfs" > "$TMP/squash-list.txt"
for path in \
    "usr/bin/spiral-installer" \
    "usr/lib/spiral-installer/spiral_installer/engine.py" \
    "usr/lib/spiral-installer/spiral_installer/cli.py" \
    "usr/share/spiral/config/debian.json" \
    "usr/share/spiral/profiles/filesystems/btrfs.json" \
    "usr/share/spiral/build-info.json" \
    "usr/share/applications/spiral-installer.desktop"; do
    grep -q "$path" "$TMP/squash-list.txt" || { echo "missing in squashfs: $path" >&2; exit 1; }
done

ssh_material=$(grep -E '(^|/)(squashfs-root/)?(root|home/[^/]+)/\.ssh(/|$)' "$TMP/squash-list.txt" || true)
if [ -n "$ssh_material" ]; then
    echo "private SSH material found in the live image:" >&2
    printf '%s\n' "$ssh_material" >&2
    exit 1
fi

if xorriso -osirrox on -indev "$ISO" -extract /boot/grub/grub.cfg "$TMP/grub.cfg" >/dev/null 2>&1; then
    for label in "Start Spiral" "Safe Graphics" "Troubleshooting"; do
        grep -q "$label" "$TMP/grub.cfg" || { echo "GRUB menu is missing: $label" >&2; exit 1; }
    done
    if grep -qE '@(KERNEL_LIVE|INITRD_LIVE|APPEND_LIVE)@' "$TMP/grub.cfg"; then
        echo "GRUB menu still contains unresolved live-build placeholders" >&2
        exit 1
    fi
    grep -q "vmlinuz" "$TMP/grub.cfg" || { echo "GRUB menu has no kernel path" >&2; exit 1; }
else
    echo "could not find /boot/grub/grub.cfg on the ISO" >&2
    exit 1
fi

if [ -n "$SHA256" ]; then
    (cd "$(dirname "$SHA256")" && sha256sum -c "$SHA256")
fi

echo "ISO validation passed: Debian $CODENAME, size $SIZE bytes"
