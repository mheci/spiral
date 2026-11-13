from __future__ import annotations

import gettext
import os
from pathlib import Path
from typing import Optional

_LOCALE_DIRS = [Path("/usr/share/spiral/locale"), Path(__file__).resolve().parents[2] / "locale"]
_translation: Optional[gettext.NullTranslations] = None


def setup(language: Optional[str] = None) -> None:
    global _translation
    language = language or os.environ.get("LANG", "").split(".")[0].split("_")[0] or "en"
    for directory in _LOCALE_DIRS:
        if not directory.is_dir():
            continue
        try:
            _translation = gettext.translation(
                "spiral", localedir=str(directory), languages=[language], fallback=False
            )
            return
        except OSError:
            continue
    _translation = gettext.NullTranslations()


def _(message: str) -> str:
    if _translation is None:
        return message
    return _translation.gettext(message)
