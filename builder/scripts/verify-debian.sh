#!/bin/sh
# Verify a Debian Release/InRelease file against the installed Debian keyring.
set -eu
if [ "$#" -ne 1 ]; then
    echo "usage: verify-debian.sh /path/to/InRelease" >&2
    exit 2
fi
exec gpgv --keyring /usr/share/keyrings/debian-archive-keyring.gpg "$1"
