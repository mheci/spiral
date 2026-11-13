from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .errors import SpiralError
from .models import ReleaseInfo

CODENAME_RE = re.compile(r"^[a-z][a-z0-9]*$")


def load_release(path: str | Path, codename: Optional[str] = None) -> ReleaseInfo:
    file_path = Path(path)
    if file_path.is_file():
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SpiralError(f"cannot read release metadata at {file_path}: {exc}") from exc
        if not isinstance(data, dict):
            raise SpiralError(f"{file_path}: release metadata must be an object")
        codename_value = str(data.get("codename", ""))
        if not CODENAME_RE.match(codename_value):
            raise SpiralError(f"{file_path}: invalid Debian codename")
        return ReleaseInfo(
            suite=str(data.get("suite", "stable")),
            codename=codename_value,
            version=str(data.get("version", "unknown")),
            source=str(data.get("source", str(file_path))),
            generated_at=str(data.get("generated_at", "")),
        )
    if codename:
        if not CODENAME_RE.match(codename):
            raise SpiralError("invalid --codename value")
        return ReleaseInfo(
            suite="stable",
            codename=codename,
            version="development",
            source="command line",
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    raise SpiralError(
        "Spiral release metadata is missing. This installer must run from a Spiral live image, "
        "or you can pass --codename for a dry run."
    )
