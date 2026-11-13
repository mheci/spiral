from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .util import redact_fields


class InstallLog:
    def __init__(
        self,
        path: str | Path,
        echo: bool = True,
        callback: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        descriptor = os.open(self.path, flags, 0o600)
        self._file = os.fdopen(descriptor, "a", encoding="utf-8")
        self._echo = echo
        self._callback = callback

    def event(
        self,
        severity: str,
        module: str,
        operation: str,
        result: str,
        **fields: Any,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "severity": severity,
            "module": module,
            "operation": operation,
            "result": result,
            **redact_fields(fields),
        }
        self._file.write(json.dumps(record, default=str, sort_keys=True) + "\n")
        self._file.flush()
        if self._callback is not None:
            try:
                self._callback(record)
            except Exception:
                pass
        if self._echo:
            detail = ""
            if fields:
                safe = redact_fields(fields)
                detail = " " + " ".join(f"{key}={value}" for key, value in sorted(safe.items()))
            stream = {
                "error": __import__("sys").stderr,
                "warning": __import__("sys").stderr,
            }.get(severity, __import__("sys").stdout)
            print(f"[{severity}] {module}: {operation}: {result}{detail}", file=stream)

    def close(self) -> None:
        try:
            self._file.close()
        except OSError:
            pass

    def __enter__(self) -> "InstallLog":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
