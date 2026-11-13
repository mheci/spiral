from __future__ import annotations

import json
import platform
import re
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional, Sequence

from .errors import DetectionError
from .models import (
    Disk,
    Firmware,
    Gpu,
    GpuVendor,
    HardwareReport,
    NetworkAdapter,
    Partition,
)
from .util import parse_kib, read_text, run_capture

PCI_DISPLAY_CLASSES = ("0300", "0301", "0302", "0380")
PCI_USB_CLASS = "0c03"


def _int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _bool(value: object) -> bool:
    return value in (True, 1, "1", "true", "True")


def _mountpoints(item: dict) -> tuple[str, ...]:
    points = item.get("mountpoints")
    if isinstance(points, list):
        return tuple(str(p) for p in points if p)
    single = item.get("mountpoint")
    if isinstance(single, str) and single:
        return (single,)
    return ()


def parse_lsblk_json(text: str) -> tuple[Disk, ...]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DetectionError("lsblk returned invalid JSON", detail=str(exc)) from exc
    devices = data.get("blockdevices") if isinstance(data, dict) else None
    if not isinstance(devices, list):
        raise DetectionError("lsblk output has no blockdevices array")

    def partition(item: dict) -> Partition:
        children = item.get("children")
        child_parts: list[Partition] = []
        if isinstance(children, list):
            for child in children:
                if not isinstance(child, dict):
                    continue
                number_match = re.search(r"(\d+)$", str(child.get("name", "")))
                child_parts.append(
                    Partition(
                        path=str(child.get("path") or ""),
                        number=int(number_match.group(1)) if number_match else 0,
                        size_bytes=_int(child.get("size")),
                        fstype=child.get("fstype") or None,
                        mountpoints=_mountpoints(child),
                        label=child.get("label") or None,
                        parttype=child.get("parttype") or None,
                        children=partition(child).children,
                    )
                )
        return Partition(
            path=str(item.get("path") or ""),
            number=0,
            size_bytes=_int(item.get("size")),
            fstype=item.get("fstype") or None,
            mountpoints=_mountpoints(item),
            label=item.get("label") or None,
            parttype=item.get("parttype") or None,
            children=tuple(child_parts),
        )

    disks: list[Disk] = []
    for item in devices:
        if not isinstance(item, dict) or item.get("type") != "disk":
            continue
        path = str(item.get("path") or "")
        if not path:
            continue
        children = item.get("children")
        parts: list[Partition] = []
        if isinstance(children, list):
            for child in children:
                if not isinstance(child, dict):
                    continue
                if child.get("type") not in ("part", "crypt", "lvm"):
                    continue
                number_match = re.search(r"(\d+)$", str(child.get("name", "")))
                nested = partition(child)
                parts.append(
                    Partition(
                        path=str(child.get("path") or ""),
                        number=int(number_match.group(1)) if number_match else 0,
                        size_bytes=_int(child.get("size")),
                        fstype=child.get("fstype") or None,
                        mountpoints=_mountpoints(child),
                        label=child.get("label") or None,
                        parttype=child.get("parttype") or None,
                        children=nested.children,
                    )
                )
        id_link = item.get("id-link") or item.get("id_link")
        disks.append(
            Disk(
                path=path,
                size_bytes=_int(item.get("size")),
                model=str(item.get("model") or "").strip(),
                serial=str(item.get("serial") or "").strip(),
                transport=str(item.get("tran") or "").strip(),
                removable=_bool(item.get("rm")),
                read_only=_bool(item.get("ro")),
                by_id=f"/dev/disk/by-id/{id_link}" if id_link else None,
                partitions=tuple(parts),
            )
        )
    return tuple(disks)


_LSPCI_TAIL_RE = re.compile(r"^(?P<name>.*?)\s*\[(?P<code>[0-9a-f]{4})\]$")


def parse_lspci_devices(
    text: str, vendor_map: Optional[dict[str, str]] = None
) -> tuple[list[Gpu], list[str]]:
    """Return display adapters and USB controllers from `lspci -Dnmm` output."""
    vendors = {
        "10de": "nvidia",
        "1002": "amd",
        "1022": "amd",
        "8086": "intel",
    }
    if vendor_map:
        vendors.update({key.lower(): value for key, value in vendor_map.items()})

    def tail(field: str) -> tuple[str, str]:
        match = _LSPCI_TAIL_RE.match(field.strip())
        if match:
            return match.group("name").strip(), match.group("code").lower()
        return field.strip(), ""

    gpus: list[Gpu] = []
    usb: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or '"' not in line:
            continue
        slot = line.split('"', 1)[0].strip()
        fields = re.findall(r'"([^"]*)"', line)
        if len(fields) < 3:
            continue
        klass_name, klass = tail(fields[0])
        vendor_name, vendor_id = tail(fields[1])
        device_name, device_id = tail(fields[2])
        if klass in PCI_DISPLAY_CLASSES:
            vendor_key = vendors.get(vendor_id, "unknown")
            try:
                vendor = GpuVendor(vendor_key)
            except ValueError:
                vendor = GpuVendor.UNKNOWN
            gpus.append(
                Gpu(
                    address=slot,
                    vendor_id=vendor_id,
                    device_id=device_id or "0000",
                    vendor=vendor,
                    model=f"{vendor_name} {device_name}".strip(),
                )
            )
        elif klass == PCI_USB_CLASS:
            usb.append(f"{vendor_name} {device_name}".strip())
    return gpus, usb


def parse_lscpu_json(text: str) -> tuple[str, str, int]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DetectionError("lscpu returned invalid JSON", detail=str(exc)) from exc
    entries = data.get("lscpu") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise DetectionError("lscpu output has no lscpu array")
    values: dict[str, str] = {}
    for entry in entries:
        if isinstance(entry, dict) and "field" in entry:
            values[str(entry["field"]).rstrip(":")] = str(entry.get("data", ""))
    arch = values.get("Architecture", "")
    model = values.get("Model name", "")
    cores = _int(values.get("CPU(s)"), 0)
    if not arch:
        raise DetectionError("lscpu did not report an architecture")
    return arch, model, cores


def parse_meminfo(text: str) -> int:
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            return parse_kib(line.split(":", 1)[1].strip())
    raise DetectionError("MemTotal missing from /proc/meminfo")


def normalize_arch(machine: str) -> str:
    return {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "i686": "i386",
        "i386": "i386",
    }.get(machine, machine)


def parse_secure_boot_efivar(raw: bytes) -> Optional[bool]:
    if len(raw) < 5:
        return None
    value = raw[4]
    if value not in (0, 1):
        return None
    return value == 1


def parse_secure_boot_text(text: str) -> Optional[bool]:
    lowered = text.lower()
    if "secureboot enabled" in lowered or "secure boot enabled" in lowered:
        return True
    if "secureboot disabled" in lowered or "secure boot disabled" in lowered:
        return False
    return None


def parse_network_sysfs(root: Path) -> tuple[NetworkAdapter, ...]:
    if not root.is_dir():
        return ()
    adapters: list[NetworkAdapter] = []
    for iface in sorted(root.iterdir()):
        name = iface.name
        if name == "lo":
            continue
        wireless = (iface / "wireless").exists() or (iface / "phy80211").exists()
        state = read_text(iface / "operstate")
        driver = None
        uevent = read_text(iface / "device" / "uevent") or ""
        for line in uevent.splitlines():
            if line.startswith("DRIVER="):
                driver = line.split("=", 1)[1] or None
        kind = "wireless" if wireless else "ethernet"
        if name.startswith(("wl", "wlan")):
            kind = "wireless"
            wireless = True
        adapters.append(
            NetworkAdapter(name=name, kind=kind, driver=driver, wireless=wireless, state=state)
        )
    return tuple(adapters)


def find_by_id(device_path: str, by_id_root: Path = Path("/dev/disk/by-id")) -> Optional[str]:
    """Resolve a stable /dev/disk/by-id name for a block device."""
    if not by_id_root.is_dir():
        return None
    candidates: list[tuple[int, str]] = []
    try:
        entries = list(by_id_root.iterdir())
    except OSError:
        return None
    for entry in entries:
        try:
            if entry.resolve() != Path(device_path).resolve():
                continue
        except OSError:
            continue
        name = entry.name
        if name.startswith("wwn-"):
            rank = 3
        elif name.startswith(("nvme-", "ata-", "virtio-", "scsi-")):
            rank = 2
        elif name.startswith(("dm-", "md-")):
            rank = 0
        else:
            rank = 1
        candidates.append((rank, str(entry)))
    if not candidates:
        return None
    return sorted(candidates)[-1][1]


def parse_audio_cards(text: str) -> tuple[str, ...]:
    cards = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and re.match(r"^\d+\s+\[", stripped):
            cards.append(stripped)
    return tuple(cards)


def detect_secure_boot(sys_root: Path = Path("/sys")) -> tuple[bool, Optional[bool]]:
    efi = (sys_root / "firmware" / "efi").is_dir()
    if not efi:
        return False, None
    efivar = sys_root / "firmware" / "efi" / "efivars" / "SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
    try:
        value = parse_secure_boot_efivar(efivar.read_bytes())
        if value is not None:
            return True, value
    except OSError:
        pass
    try:
        result = run_capture(["mokutil", "--sb-state"], timeout=5)
        if result.returncode == 0:
            return True, parse_secure_boot_text(result.stdout)
    except (OSError, Exception):
        pass
    return True, None


@dataclass(frozen=True)
class NvidiaDetection:
    available: bool
    package: Optional[str]
    supported: Optional[bool]


def detect_nvidia(run: Callable[..., object] = run_capture) -> NvidiaDetection:
    """Use Debian's nvidia-detect where present. It never assumes GPU support."""
    if not which("nvidia-detect"):
        return NvidiaDetection(available=False, package=None, supported=None)
    try:
        result = run(["nvidia-detect"], timeout=20)
        text = (getattr(result, "stdout", "") or "") + (getattr(result, "stderr", "") or "")
    except Exception:
        return NvidiaDetection(available=True, package=None, supported=None)
    package = parse_nvidia_detect_output(text)
    if package:
        return NvidiaDetection(available=True, package=package, supported=True)
    if re.search(r"not supported by any driver", text, re.IGNORECASE):
        return NvidiaDetection(available=True, package=None, supported=False)
    return NvidiaDetection(available=True, package=None, supported=None)


def detect_nvidia_recommendation(run: Callable[..., object] = run_capture) -> Optional[str]:
    return detect_nvidia(run).package


def parse_nvidia_detect_output(text: str) -> Optional[str]:
    if re.search(r"not supported by any driver", text, re.IGNORECASE):
        return None
    match = re.search(r"\b(nvidia-(?:legacy-[0-9]+xx-)?driver)\b", text)
    if match:
        return match.group(1)
    match = re.search(r"\b(nvidia-driver-\d+)\b", text)
    if match:
        return match.group(1)
    return None


def collect_hardware(
    gpu_vendors: dict[str, str],
    sys_root: Path = Path("/sys"),
    run: Callable[..., object] = run_capture,
) -> HardwareReport:
    warnings: list[str] = []
    arch = normalize_arch(platform.machine())
    cpu_model = ""
    cpu_cores = 0
    ram_mib = 0

    try:
        result = run(["lscpu", "-J"], timeout=10)
        arch_raw, cpu_model, cpu_cores = parse_lscpu_json(result.stdout)
        arch = normalize_arch(arch_raw.lower())
    except Exception as exc:
        warnings.append(f"lscpu: {exc}")

    try:
        ram_mib = parse_meminfo(Path("/proc/meminfo").read_text(encoding="utf-8"))
    except Exception as exc:
        warnings.append(f"meminfo: {exc}")

    gpus: list[Gpu] = []
    usb: list[str] = []
    try:
        result = run(["lspci", "-Dnmm"], timeout=10)
        gpus, usb = parse_lspci_devices(result.stdout, gpu_vendors)
    except Exception as exc:
        warnings.append(f"lspci: {exc}")

    disks: tuple[Disk, ...] = ()
    try:
        result = run(
            [
                "lsblk",
                "-J",
                "-b",
                "-o",
                "NAME,PATH,SIZE,TYPE,MODEL,SERIAL,TRAN,RM,RO,FSTYPE,MOUNTPOINTS,PARTTYPE,LABEL",
            ],
            timeout=10,
        )
        disks = parse_lsblk_json(result.stdout)
        disks = tuple(
            disk if disk.by_id else replace(disk, by_id=find_by_id(disk.path))
            for disk in disks
        )
    except Exception as exc:
        warnings.append(f"lsblk: {exc}")

    uefi, secure_boot = detect_secure_boot(sys_root)
    virtualization = None
    try:
        result = run(["systemd-detect-virt"], timeout=5)
        virtualization = (result.stdout or "").strip() or None
        if virtualization == "none":
            virtualization = None
    except Exception as exc:
        warnings.append(f"systemd-detect-virt: {exc}")

    try:
        network = parse_network_sysfs(sys_root / "class" / "net")
    except Exception as exc:
        warnings.append(f"network: {exc}")
        network = ()

    try:
        audio = parse_audio_cards(
            Path("/proc/asound/cards").read_text(encoding="utf-8", errors="replace")
        )
    except OSError:
        audio = ()

    bluetooth = (sys_root / "class" / "bluetooth").is_dir() and any(
        (sys_root / "class" / "bluetooth").iterdir()
    )

    return HardwareReport(
        architecture=arch,
        cpu_model=cpu_model,
        cpu_cores=cpu_cores,
        ram_mib=ram_mib,
        firmware=Firmware.UEFI if uefi else Firmware.BIOS,
        secure_boot=secure_boot,
        gpus=tuple(gpus),
        network=network,
        disks=disks,
        virtualization=virtualization,
        bluetooth=bluetooth,
        audio_controllers=audio,
        usb_controllers=tuple(usb),
        warnings=tuple(warnings),
    )
