#!/bin/bash
set -Eeuo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "install-build-deps.sh must run as root (the CI container does this)" >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    live-build \
    debootstrap \
    xorriso \
    squashfs-tools \
    grub-pc-bin \
    grub-efi-amd64-bin \
    grub-efi-amd64-signed \
    shim-signed \
    memtest86+ \
    isolinux \
    syslinux-utils \
    dosfstools \
    mtools \
    gpgv \
    debian-archive-keyring \
    gnupg \
    python3 \
    ca-certificates \
    curl \
    xz-utils \
    zstd \
    file \
    qemu-system-x86 \
    qemu-utils \
    ovmf
