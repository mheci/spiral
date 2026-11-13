from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SpiralError(Exception):
    """Base error. The message is safe to show to the user."""

    message: str
    detail: Optional[str] = None

    def __str__(self) -> str:
        return self.message


class ConfigError(SpiralError):
    pass


class ValidationError(SpiralError):
    def __init__(self, message: str, issues: Optional[list[str]] = None) -> None:
        super().__init__(message)
        self.issues = issues or []


class DetectionError(SpiralError):
    pass


class InstallError(SpiralError):
    def __init__(
        self,
        message: str,
        phase: Optional[str] = None,
        completed: Optional[list[str]] = None,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message, detail)
        self.phase = phase
        self.completed = completed or []


class CommandError(InstallError):
    def __init__(self, argv: list[str], returncode: int, stderr: str = "") -> None:
        safe = " ".join(argv)
        super().__init__(
            f"Command failed with exit code {returncode}: {safe}",
            detail=stderr[-4000:] if stderr else None,
        )
        self.argv = argv
        self.returncode = returncode
