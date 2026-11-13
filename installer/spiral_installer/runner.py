from __future__ import annotations

import os
import shlex
import subprocess
from typing import Optional, Sequence

from .errors import CommandError, InstallError
from .log import InstallLog
from .util import Secret


class CommandRunner:
    """Executes argv arrays only. A shell is never used."""

    def __init__(self, log: InstallLog, echo_output: bool = False) -> None:
        self.log = log
        self.echo_output = echo_output
        self._env = {
            **os.environ,
            "DEBIAN_FRONTEND": "noninteractive",
            "APT_LISTCHANGES_FRONTEND": "none",
            "LC_ALL": "C.UTF-8",
        }

    def run(
        self,
        argv: Sequence[str],
        stdin_secret: Optional[Secret] = None,
        stdin_text: Optional[str] = None,
        timeout: Optional[int] = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        command = [str(item) for item in argv]
        self.log.event("info", "command", "run", shlex.join(command))
        input_text = None
        if stdin_secret is not None:
            input_text = stdin_secret.reveal() + "\n"
        elif stdin_text is not None:
            input_text = stdin_text
        try:
            result = subprocess.run(
                command,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._env,
            )
        except FileNotFoundError as exc:
            raise InstallError(f"required program is not installed: {command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise InstallError(f"command timed out after {timeout}s: {shlex.join(command)}") from exc
        except OSError as exc:
            raise InstallError(f"could not execute {command[0]}: {exc}") from exc
        if self.echo_output and result.stdout:
            print(result.stdout, end="")
        if result.returncode != 0:
            self.log.event(
                "error",
                "command",
                "failed",
                f"exit {result.returncode}",
                argv=shlex.join(command),
                stderr=result.stderr[-4000:],
            )
            if check:
                raise CommandError(command, result.returncode, result.stderr)
        else:
            self.log.event("info", "command", "ok", shlex.join(command))
        return result
