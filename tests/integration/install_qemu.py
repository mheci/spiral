#!/usr/bin/env python3
"""Unattended Spiral installation test in QEMU over a serial console.

This is an integration test, not a release component. It creates a disposable
disk, installs from the live ISO, reboots, logs into the installed system and
checks the filesystem-specific results.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pexpect

LIVE_PROMPT = r"spiral@spiral:[^$]*\$ "
INSTALL_PROMPT = r"testuser@spiral-test:[^$]*\$ "
PASSWORD = "spiral-test-password"
LUKS_PASSPHRASE = "spiral-test-luks-passphrase"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _clean(text: str) -> str:
    return text.replace("\r", "").replace("\n", "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iso", required=True)
    parser.add_argument("--filesystem", choices=("ext4", "btrfs"), required=True)
    parser.add_argument("--choices", required=True, help="choices file name under /usr/share/spiral/test")
    parser.add_argument("--firmware", choices=("bios", "uefi"), default="bios")
    parser.add_argument("--luks", action="store_true")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--boot-timeout", type=int, default=600)
    parser.add_argument("--install-timeout", type=int, default=5400)
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    disk = workdir / "disk.img"
    serial_path = workdir / f"serial-{args.filesystem}.log"
    result_path = workdir / f"result-{args.filesystem}.json"
    for path in (disk, serial_path, result_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    with disk.open("wb") as handle:
        handle.truncate(25 * 1024**3)

    command = [
        "qemu-system-x86_64",
        "-m", "4096",
        "-smp", "2",
        "-machine", "q35",
        "-display", "none",
        "-monitor", "none",
        "-serial", "stdio",
        "-drive", f"file={disk},if=none,format=raw,id=target",
        "-device", "virtio-blk-pci,drive=target,serial=SPIRALTEST",
        "-cdrom", str(Path(args.iso).resolve()),
        "-boot", "order=c,once=d",
        "-nic", "user,model=virtio-net-pci",
    ]
    if args.firmware == "uefi":
        candidates = (
            (Path("/usr/share/OVMF/OVMF_CODE_4M.fd"), Path("/usr/share/OVMF/OVMF_VARS_4M.fd")),
            (Path("/usr/share/OVMF/OVMF_CODE.fd"), Path("/usr/share/OVMF/OVMF_VARS.fd")),
            (Path("/usr/share/ovmf/OVMF.fd"), Path("/usr/share/ovmf/OVMF_VARS.fd")),
        )
        code, vars_source = next(
            ((code, variables) for code, variables in candidates if code.is_file() and variables.is_file()),
            (None, None),
        )
        if code is None:
            raise RuntimeError("OVMF firmware was not found")
        variables = workdir / "OVMF_VARS.fd"
        shutil.copyfile(vars_source, variables)
        command += [
            "-drive", f"if=pflash,format=raw,readonly=on,file={code}",
            "-drive", f"if=pflash,format=raw,file={variables}",
        ]
    if os.access("/dev/kvm", os.R_OK | os.W_OK):
        command += ["-enable-kvm", "-cpu", "host"]

    serial = serial_path.open("w", encoding="utf-8", errors="replace")
    child = pexpect.spawn(
        command[0],
        command[1:],
        encoding="utf-8",
        codec_errors="replace",
        timeout=args.boot_timeout,
    )
    child.logfile_read = serial
    results: dict = {"filesystem": args.filesystem, "checks": {}, "log": str(serial_path)}
    try:
        child.expect(LIVE_PROMPT, timeout=args.boot_timeout)
        print("live environment reached")

        choices = f"/usr/share/spiral/test/{args.choices}"
        base_command = (
            f"sudo spiral-installer --unattended {choices} "
            "--user-password-file /usr/share/spiral/test/password.txt "
            "--luks-passphrase-file /usr/share/spiral/test/luks.txt "
            "--log /tmp/install.log"
        )

        child.sendline(base_command)
        matched = child.expect(["requires --confirm", LIVE_PROMPT], timeout=1200)
        if matched == 1:
            raise RuntimeError("installer rejected the unattended choices before asking for confirmation")
        first = child.before + (child.after or "")
        child.expect(LIVE_PROMPT, timeout=120)
        first += child.before
        clean = _clean(first)
        phrase_match = re.search(r"--confirm '([^']+)'", clean)
        ack_match = re.search(r"--ack ([a-z0-9,\-]+)", clean)
        if not phrase_match:
            raise RuntimeError("could not read the required confirmation phrase")
        phrase = phrase_match.group(1)
        ack = ack_match.group(1) if ack_match else ""
        results["phrase"] = phrase
        results["ack"] = ack

        full_command = f"{base_command} --confirm '{phrase}'"
        if ack:
            full_command += f" --ack {ack}"
        child.sendline(full_command)
        outcome = child.expect(
            ["Installation complete", "Installation failed"],
            timeout=args.install_timeout,
        )
        if outcome != 0:
            raise RuntimeError("the installer reported a failure")
        child.expect(LIVE_PROMPT, timeout=120)
        results["installation_complete"] = True

        child.sendline("sudo reboot")
        if args.luks:
            unlocked = child.expect(
                [r"(?i)(please unlock disk|enter passphrase)", "spiral-test login:"],
                timeout=args.boot_timeout,
            )
            if unlocked == 0:
                child.sendline(LUKS_PASSPHRASE)
                child.expect("spiral-test login:", timeout=180)
        else:
            child.expect("spiral-test login:", timeout=args.boot_timeout)
        child.sendline("testuser")
        child.expect("Password:", timeout=60)
        child.sendline(PASSWORD)
        child.expect(INSTALL_PROMPT, timeout=180)
        print("installed system login succeeded")
        child.sendline("stty -echo")
        child.expect(INSTALL_PROMPT, timeout=30)

        def clean(text: str, command: str) -> str:
            text = ANSI_RE.sub("", text).replace("\r", "")
            lines = [line for line in text.splitlines() if line.strip()]
            if lines and command in lines[0]:
                lines = lines[1:]
            return "\n".join(lines).strip()

        def shell(sh_command: str, timeout: int = 180) -> str:
            child.sendline(sh_command)
            child.expect(INSTALL_PROMPT, timeout=timeout)
            return clean(child.before, sh_command)

        def sudo(sh_command: str, timeout: int = 300) -> str:
            child.sendline("sudo " + sh_command)
            result = child.expect(
                [r"\[sudo\] password for testuser:", INSTALL_PROMPT],
                timeout=timeout,
            )
            if result == 0:
                child.sendline(PASSWORD)
                child.expect(INSTALL_PROMPT, timeout=timeout)
            return clean(child.before, "sudo " + sh_command)

        hostname = shell("hostnamectl --static").strip()
        root_fs = shell("findmnt -no FSTYPE /").strip()
        root_options = shell("findmnt -no OPTIONS /").strip()
        system_state = shell("systemctl is-system-running || true").strip()
        swap = sudo("swapon --show").strip()
        results["checks"].update(
            {
                "hostname": hostname,
                "root_filesystem": root_fs,
                "root_options": root_options,
                "system_state": system_state,
                "swap": swap,
            }
        )
        if hostname != "spiral-test":
            raise RuntimeError(f"unexpected hostname: {hostname!r}")
        if root_fs != args.filesystem:
            raise RuntimeError(f"unexpected root filesystem: {root_fs!r}")
        if "running" not in system_state and "degraded" not in system_state:
            raise RuntimeError(f"systemd is not running: {system_state!r}")
        if "zram" not in swap:
            raise RuntimeError(f"zram swap is not active: {swap!r}")

        if args.filesystem == "btrfs":
            subvolumes = sudo("btrfs subvolume list /")
            results["checks"]["btrfs_subvolumes"] = subvolumes.strip()
            for name in ("@home", "@var_log", "@cache"):
                if name not in subvolumes:
                    raise RuntimeError(f"missing BTRFS subvolume: {name}")
            if "subvol=@" not in root_options and "subvol=/@" not in root_options:
                raise RuntimeError(f"root is not mounted with subvol=@: {root_options!r}")

        fstab = sudo("cat /etc/fstab")
        results["checks"]["fstab"] = fstab.strip()
        if args.filesystem == "btrfs" and "subvol=@" not in fstab:
            raise RuntimeError("BTRFS root subvolume is missing from fstab")

        result_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        child.sendline("sudo poweroff")
        child.expect(pexpect.EOF, timeout=180)
        print("install test passed")
        return 0
    except Exception as exc:
        tail = ""
        try:
            tail = serial_path.read_text(encoding="utf-8", errors="replace")[-6000:]
        except OSError:
            pass
        print(f"install test failed: {exc}", file=sys.stderr)
        print(tail, file=sys.stderr)
        results["error"] = str(exc)
        print(json.dumps(results, indent=2))
        result_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        return 1
    finally:
        serial.close()
        if child.isalive():
            child.terminate(force=True)
        try:
            disk.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
