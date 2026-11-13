from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from .errors import ConfigError

PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.\-]+$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*$")
SUBVOL_RE = re.compile(r"^@[a-z0-9_]+$")
ARCH_RE = re.compile(r"^[a-z0-9]+$")


def fail(context: str, message: str) -> None:
    raise ConfigError(f"{context}: {message}")


def mapping(value: Any, context: str) -> dict:
    if not isinstance(value, dict):
        fail(context, f"expected an object, got {type(value).__name__}")
    return value


def check_keys(
    data: dict,
    context: str,
    allowed: Iterable[str],
    required: Iterable[str] = (),
) -> None:
    allowed_set = set(allowed)
    required_set = set(required)
    unknown = sorted(set(data) - allowed_set)
    missing = sorted(required_set - set(data))
    problems = []
    if unknown:
        problems.append(f"unknown keys: {', '.join(unknown)}")
    if missing:
        problems.append(f"missing keys: {', '.join(missing)}")
    if problems:
        fail(context, "; ".join(problems))


_MISSING = object()


def text(
    data: dict,
    key: str,
    context: str,
    default: Any = _MISSING,
    allow_empty: bool = False,
    choices: Optional[Iterable[str]] = None,
) -> str:
    if key not in data:
        if default is _MISSING:
            fail(context, f"missing key '{key}'")
        return default
    value = data[key]
    if not isinstance(value, str):
        fail(context, f"'{key}' must be a string")
    if not value and not allow_empty:
        fail(context, f"'{key}' must not be empty")
    if choices is not None and value not in set(choices):
        fail(context, f"'{key}' must be one of: {', '.join(sorted(choices))}")
    return value


def integer(data: dict, key: str, context: str, default: Any = _MISSING) -> int:
    if key not in data:
        if default is _MISSING:
            fail(context, f"missing key '{key}'")
        return default
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        fail(context, f"'{key}' must be an integer")
    return value


def number(data: dict, key: str, context: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(context, f"'{key}' must be a number")
    return float(value)


def boolean(data: dict, key: str, context: str, default: Any = _MISSING) -> bool:
    if key not in data:
        if default is _MISSING:
            fail(context, f"missing key '{key}'")
        return default
    value = data[key]
    if not isinstance(value, bool):
        fail(context, f"'{key}' must be a boolean")
    return value


def string_list(
    data: dict,
    key: str,
    context: str,
    required: bool = True,
    default: Optional[list] = None,
) -> list[str]:
    if key not in data:
        if required:
            fail(context, f"missing key '{key}'")
        return list(default or [])
    value = data[key]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        fail(context, f"'{key}' must be an array of strings")
    return list(value)


def optional_text(data: dict, key: str, context: str) -> Optional[str]:
    if key not in data or data[key] is None:
        return None
    return text(data, key, context)


def identifier(data: dict, key: str, context: str) -> str:
    value = text(data, key, context)
    if not ID_RE.match(value):
        fail(context, f"'{key}' must match {ID_RE.pattern}")
    return value


def package_name(value: str, context: str) -> None:
    probe = value.replace("{arch}", "amd64")
    if not PACKAGE_RE.match(probe):
        fail(context, f"invalid Debian package name: {value!r}")


def package_list(data: dict, key: str, context: str, required: bool = True) -> list[str]:
    values = string_list(data, key, context, required=required)
    for value in values:
        package_name(value, f"{context}:{key}")
    return values


def arch(data: dict, key: str, context: str) -> str:
    value = text(data, key, context)
    if not ARCH_RE.match(value):
        fail(context, f"'{key}' is not a valid Debian architecture")
    return value
