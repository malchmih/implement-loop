"""Harness CLI adapters and live output persistence."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Literal

from .errors import LoopError

HarnessName = Literal["opencode", "copilot"]


@dataclass(frozen=True)
class HarnessSettings:
    """User-selected model and reasoning values for one harness."""

    model: str
    model_variant: str | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class HarnessSpec:
    """Pinned invocation details for one supported harness."""

    name: HarnessName
    executable: str
    display_name: str
    model: str
    model_variant: str | None
    reasoning_effort: str | None
    skill_reference: str

    @property
    def model_selector(self) -> str:
        if self.model_variant is None:
            return self.model
        return f"{self.model}#{self.model_variant}"

    @property
    def model_summary(self) -> str:
        if self.model_variant is not None:
            return self.model_selector
        if self.reasoning_effort is not None:
            return f"{self.model} / {self.reasoning_effort} reasoning"
        return self.model

    def metadata(self) -> dict[str, str | None]:
        return {
            "harness": self.name,
            "model": self.model,
            "model_variant": self.model_variant,
            "reasoning_effort": self.reasoning_effort,
        }

    def command(self, prompt: str) -> list[str]:
        if self.name == "opencode":
            return [
                self.executable,
                "run",
                "--standalone",
                "--auto",
                "--model",
                self.model_selector,
                prompt,
            ]

        assert self.reasoning_effort is not None
        return [
            self.executable,
            "--trust-folder",
            "-p",
            prompt,
            "--model",
            self.model,
            "--reasoning-effort",
            self.reasoning_effort,
            "--stream",
            "on",
            "--output-format",
            "text",
            "--allow-all-tools",
        ]


_HARNESS_SPECS: dict[str, HarnessSpec] = {
    "opencode": HarnessSpec(
        name="opencode",
        executable="opencode",
        display_name="OpenCode",
        model="github-copilot/gpt-6-luna",
        model_variant="max",
        reasoning_effort=None,
        skill_reference="@implement",
    ),
    "copilot": HarnessSpec(
        name="copilot",
        executable="copilot",
        display_name="Copilot",
        model="gpt-6-luna",
        model_variant=None,
        reasoning_effort="max",
        skill_reference="/implement",
    ),
}


def _harness_spec(name: str) -> HarnessSpec:
    try:
        return _HARNESS_SPECS[name]
    except KeyError as error:
        raise LoopError(
            f"Unknown harness {name!r}; choose either 'opencode' or 'copilot'."
        ) from error


def default_harness_settings(name: str) -> HarnessSettings:
    """Return the pinned model defaults for a supported harness."""

    spec = _harness_spec(name)
    return HarnessSettings(
        model=spec.model,
        model_variant=spec.model_variant,
        reasoning_effort=spec.reasoning_effort,
    )


def select_harness(name: str, settings: HarnessSettings | None = None) -> HarnessSpec:
    """Return the documented invocation with the selected model settings."""

    spec = _harness_spec(name)
    selected = settings or default_harness_settings(name)
    return HarnessSpec(
        name=spec.name,
        executable=spec.executable,
        display_name=spec.display_name,
        model=selected.model,
        model_variant=selected.model_variant,
        reasoning_effort=selected.reasoning_effort,
        skill_reference=spec.skill_reference,
    )


@dataclass(frozen=True)
class ProcessOutcome:
    """Observable result of one fresh harness process."""

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


def run_harness(
    harness: HarnessSpec,
    prompt: str,
    cwd: Path,
    log_path: Path,
) -> ProcessOutcome:
    """Start one fresh harness process and stream/persist its native text output."""

    return _run_process(harness.command(prompt), cwd, log_path, harness.display_name)


def _run_process(
    command: Sequence[str],
    cwd: Path,
    log_path: Path,
    display_name: str,
) -> ProcessOutcome:
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
        raise LoopError(
            f"Cannot create private {display_name} output log {log_path}: {error}"
        ) from error
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
        spawn_error = f"{display_name} could not be started: {error}"
        with os.fdopen(descriptor, "wb"):
            pass
        return ProcessOutcome(
            exit_code=None,
            log_persisted=True,
            log_error=None,
            stream_error=None,
            spawn_error=spawn_error,
            started_at=started_at,
            finished_at=_timestamp(),
        )

    output: BinaryIO | None = getattr(sys.stdout, "buffer", None)
    try:
        with os.fdopen(descriptor, "wb") as log_file:
            if process.stdout is None:
                log_persisted = False
                log_error = f"{display_name} output stream was unavailable."
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
