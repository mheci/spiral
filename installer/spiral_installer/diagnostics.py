from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .errors import SpiralError


def export_diagnostics(source: str | Path, destination: str | Path) -> Path:
    source_path = Path(source)
    destination_path = Path(destination)
    if not source_path.is_dir():
        raise SpiralError(f"no diagnostics directory at {source_path}")
    if not destination_path.is_dir():
        raise SpiralError(f"export destination is not a directory: {destination_path}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = destination_path / f"spiral-diagnostics-{stamp}"
    try:
        target.mkdir(parents=False, exist_ok=False)
        for item in sorted(source_path.iterdir()):
            if item.is_file():
                shutil.copy2(item, target / item.name)
    except OSError as exc:
        raise SpiralError(f"could not export diagnostics: {exc}") from exc
    return target


def write_hardware_report(report: object, path: str | Path) -> None:
    from dataclasses import asdict

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(asdict(report), indent=2, default=str) + "\n", encoding="utf-8")
    target.chmod(0o600)
