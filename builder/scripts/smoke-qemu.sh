#!/bin/bash
# Boot smoke test. Structural validation is separate.
set -Eeuo pipefail

ISO=""
MODE="bios"
TIMEOUT=240

usage() { echo "usage: smoke-qemu.sh --iso FILE [--mode bios|uefi] [--timeout SECONDS]" >&2; exit 2; }
while [ "$#" -gt 0 ]; do
    case "$1" in
        --iso) ISO=$2; shift 2 ;;
        --mode) MODE=$2; shift 2 ;;
        --timeout) TIMEOUT=$2; shift 2 ;;
        *) usage ;;
    esac
done
[ -f "$ISO" ] || { echo "ISO not found: $ISO" >&2; exit 1; }
command -v qemu-system-x86_64 >/dev/null || { echo "qemu-system-x86_64 is missing" >&2; exit 1; }

LOG=$(mktemp)
VARS=$(mktemp)
trap 'rm -f "$LOG" "$VARS"' EXIT

args=(
    -m 3072 -smp 2 -machine q35 -no-reboot
    -cdrom "$ISO" -boot d
    -display none -serial "file:$LOG" -monitor none
)
if [ -r /dev/kvm ]; then
    args+=(-enable-kvm)
fi

if [ "$MODE" = "uefi" ]; then
    CODE=""
    for candidate in \
        /usr/share/OVMF/OVMF_CODE_4M.fd \
        /usr/share/OVMF/OVMF_CODE.fd \
        /usr/share/ovmf/OVMF.fd; do
        if [ -f "$candidate" ]; then CODE=$candidate; break; fi
    done
    [ -n "$CODE" ] || { echo "OVMF firmware was not found" >&2; exit 1; }
    case "$CODE" in
        *OVMF_CODE_4M.fd) VARS_SRC=/usr/share/OVMF/OVMF_VARS_4M.fd ;;
        *OVMF_CODE.fd) VARS_SRC=/usr/share/OVMF/OVMF_VARS.fd ;;
        *) VARS_SRC=/usr/share/ovmf/OVMF_VARS.fd ;;
    esac
    [ -f "$VARS_SRC" ] || { echo "OVMF variables file was not found" >&2; exit 1; }
    cp "$VARS_SRC" "$VARS"
    args+=(
        -drive "if=pflash,format=raw,readonly=on,file=$CODE"
        -drive "if=pflash,format=raw,file=$VARS"
    )
fi

set +e
timeout --signal=TERM "$TIMEOUT" qemu-system-x86_64 "${args[@]}" >/dev/null 2>&1
set -e

if grep -qi "Kernel panic" "$LOG"; then
    echo "kernel panic during $MODE boot" >&2
    tail -n 80 "$LOG" >&2
    exit 1
fi
if grep -qiE "Reached target|Startup finished" "$LOG"; then
    echo "$MODE boot smoke test passed"
    exit 0
fi
echo "$MODE boot did not reach a systemd target within ${TIMEOUT}s" >&2
tail -n 120 "$LOG" >&2
exit 1
