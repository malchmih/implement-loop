"""Copilot prompt-mode invocation and live output persistence."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from .errors import LoopError


@dataclass(frozen=True)
class ProcessOutcome:
    """Observable result of one fresh Copilot process."""

    exit_code: int | None
    log_persisted: bool
    log_error: str | None
    stream_error: str | None
    spawn_error: str | None
    started_at: str
    finished_at: str

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def run_copilot(prompt: str, cwd: Path, log_path: Path) -> ProcessOutcome:
    """Run a new Copilot prompt session, streaming and privately recording output."""

    command = [
        "copilot",
        "-p",
        prompt,
        "--model",
        "gpt-6-luna",
        "--reasoning-effort",
        "max",
        "--stream",
        "on",
        "--output-format",
        "text",
        "--allow-all-tools",
    ]
    environment = os.environ.copy()
    if "NO_COLOR" in environment or not sys.stdout.isatty():
        environment["NO_COLOR"] = "1"
        environment["FORCE_COLOR"] = "0"
    else:
        environment["FORCE_COLOR"] = "1"

    started_at = _timestamp()
    try:
        descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as error:
        raise LoopError(f"Cannot create private Copilot output log {log_path}: {error}") from error
    process: subprocess.Popen[bytes] | None = None
    exit_code: int | None = None
    log_error: str | None = None
    stream_error: str | None = None
    log_persisted = True

    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdin=None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as error:
        log_error = f"Copilot could not be started: {error}"
        with os.fdopen(descriptor, "wb"):
            pass
        return ProcessOutcome(
            exit_code=None,
            log_persisted=True,
            log_error=None,
            stream_error=None,
            spawn_error=log_error,
            started_at=started_at,
            finished_at=_timestamp(),
        )

    output: BinaryIO | None = getattr(sys.stdout, "buffer", None)
    try:
        with os.fdopen(descriptor, "wb") as log_file:
            if process.stdout is None:
                log_persisted = False
                log_error = "Copilot output stream was unavailable."
            else:
                while True:
                    chunk = process.stdout.read(8192)
                    if not chunk:
                        break
                    if log_persisted:
                        try:
                            log_file.write(chunk)
                            log_file.flush()
                        except OSError as error:
                            log_persisted = False
                            log_error = str(error)
                    if stream_error is None:
                        try:
                            if output is None:
                                sys.stdout.write(chunk.decode("utf-8", errors="replace"))
                                sys.stdout.flush()
                            else:
                                output.write(chunk)
                                output.flush()
                        except (BrokenPipeError, OSError) as error:
                            stream_error = str(error)
                process.stdout.close()
    except OSError as error:
        log_persisted = False
        log_error = str(error)
    exit_code = process.wait()
    return ProcessOutcome(
        exit_code=exit_code,
        log_persisted=log_persisted,
        log_error=log_error,
        stream_error=stream_error,
        spawn_error=None,
        started_at=started_at,
        finished_at=_timestamp(),
    )
