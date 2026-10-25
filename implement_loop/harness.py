"""Harness CLI adapters and live output persistence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
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
    output_format: Literal["text", "json"]
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
                "--format",
                self.output_format,
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
            self.output_format,
            "--allow-all-tools",
        ]


_HARNESS_SPECS: dict[str, HarnessSpec] = {
    "opencode": HarnessSpec(
        name="opencode",
        executable="opencode",
        display_name="OpenCode",
        output_format="json",
        model="github-copilot/gpt-6-luna",
        model_variant="max",
        reasoning_effort=None,
        skill_reference="@implement",
    ),
    "copilot": HarnessSpec(
        name="copilot",
        executable="copilot",
        display_name="Copilot",
        output_format="text",
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
        output_format=spec.output_format,
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


class _OpenCodeRenderer:
    """Render completed OpenCode JSON-lines events as a compact transcript."""

    def __init__(self, write: Callable[[str], None], *, color: bool) -> None:
        self._write = write
        self._color = color
        self._pending = bytearray()

    def feed(self, chunk: bytes) -> None:
        self._pending.extend(chunk)
        while True:
            try:
                boundary = self._pending.index(ord("\n"))
            except ValueError:
                return
            line = bytes(self._pending[:boundary])
            del self._pending[: boundary + 1]
            self._render_line(line)

    def finish(self) -> None:
        if self._pending:
            line = bytes(self._pending)
            self._pending.clear()
            self._render_line(line)

    def _render_line(self, raw_line: bytes) -> None:
        if raw_line.endswith(b"\r"):
            raw_line = raw_line[:-1]
        if not raw_line:
            return
        try:
            line = raw_line.decode("utf-8")
            record = json.loads(line)
        except (UnicodeDecodeError, ValueError, RecursionError):
            self._render_block(
                ["OpenCode diagnostic", *self._indented(raw_line.decode("utf-8", "replace"))]
            )
            return

        if not isinstance(record, dict):
            self._render_fallback("OpenCode output", self._compact(record))
            return
        event_type = record.get("type")
        if not isinstance(event_type, str):
            self._render_fallback("OpenCode output", self._compact(record))
            return

        payload = record.get("data")
        if not isinstance(payload, dict) or not payload:
            payload = record
        if event_type in {"step_start", "step_finish"}:
            part = payload.get("part")
            expected_part_type = "step-start" if event_type == "step_start" else "step-finish"
            if isinstance(part, dict) and part.get("type") == expected_part_type:
                return
            self._render_fallback(f"OpenCode {event_type} event", self._compact(record))
            return
        if event_type == "reasoning":
            part = payload.get("part")
            if (
                isinstance(part, dict)
                and part.get("type") == "reasoning"
                and isinstance(part.get("text"), str)
            ):
                return
            self._render_fallback("OpenCode reasoning event", "Reasoning content is not displayed.")
            return
        if event_type == "text":
            part = payload.get("part")
            text = part.get("text") if isinstance(part, dict) else payload.get("text")
            if isinstance(text, str):
                self._render_text(text)
                return
            self._render_fallback("OpenCode text event", self._compact(record))
            return
        if event_type == "tool_use":
            self._render_tool(record, payload)
            return
        if event_type == "error":
            error = payload.get("error")
            if error is None:
                self._render_fallback("OpenCode error event", self._compact(record))
            else:
                self._render_error("OpenCode error", error)
            return
        self._render_fallback(f"OpenCode event: {event_type}", self._compact(record))

    def _render_text(self, text: str) -> None:
        label = self._style("Assistant", "1")
        if not text:
            self._render_block([label, "(empty assistant text)"])
            return
        rendered = f"{label}\n{text}"
        if not text.endswith("\n"):
            rendered += "\n"
        if not text.endswith("\n\n"):
            rendered += "\n"
        self._write(rendered)

    def _render_tool(self, record: dict[str, Any], payload: dict[str, Any]) -> None:
        part = payload.get("part")
        if not isinstance(part, dict):
            self._render_fallback("OpenCode tool event", self._compact(record))
            return
        state = part.get("state")
        if not isinstance(state, dict):
            self._render_fallback("OpenCode tool event", self._compact(record))
            return

        status = state.get("status")
        normalized_status = status.casefold() if isinstance(status, str) else None
        error = state.get("error")
        if normalized_status in {"error", "failed"} or error is not None:
            failed = True
        elif normalized_status in {"completed", "success", "succeeded"}:
            failed = False
        else:
            self._render_fallback("OpenCode tool event", self._compact(record))
            return

        tool = part.get("tool", part.get("name", payload.get("tool")))
        if isinstance(tool, dict):
            tool = tool.get("name")
        tool_name = tool.strip() if isinstance(tool, str) and tool.strip() else "unknown tool"
        input_value = state.get("input", part.get("input", payload.get("input")))
        title = state.get("title", part.get("title", payload.get("title")))
        if (
            not isinstance(title, str)
            or not title.strip()
            or title.strip().casefold() == tool_name.casefold()
        ):
            title = "Tool result"
        else:
            title = " ".join(title.splitlines()).strip()

        marker = "✗" if failed else "✓"
        marker_color = "31" if failed else "32"
        lines = [f"{self._style(marker, marker_color)} {self._style(title, '1')}"]
        lines.append(f"  {self._style(f'tool: {tool_name}', '2')}")
        if input_value is not None:
            input_text = self._compact(input_value)
            if len(input_text) > 240:
                input_text = input_text[:237] + "..."
            lines.append(f"  input: {input_text}")

        output = state.get("output")
        if failed:
            error_text = (
                self._display_value(error) if error is not None else "No error details available."
            )
            lines.append("  error:")
            lines.extend(self._indented(error_text, prefix="    "))
            if output is not None:
                lines.append("  output:")
                lines.extend(self._indented(self._display_value(output), prefix="    "))
            self._render_block(lines)
            return

        if output is None or output == "":
            lines.append("  (no output)")
            self._render_block(lines)
            return
        output_lines = self._display_value(output).splitlines()
        if not output_lines:
            lines.append("  (no output)")
        else:
            lines.extend(f"    {line}" for line in output_lines[:5])
            omitted = len(output_lines) - 5
            if omitted > 0:
                lines.append(f"    ... {omitted} additional line(s) omitted")
        self._render_block(lines)

    def _render_error(self, title: str, error: Any) -> None:
        lines = [f"{self._style('✗', '31')} {self._style(title, '1')}"]
        lines.extend(self._indented(self._display_value(error)))
        self._render_block(lines)

    def _render_fallback(self, title: str, details: str) -> None:
        self._render_block([f"? {title}", *self._indented(details)])

    def _render_block(self, lines: list[str]) -> None:
        self._write("\n".join(lines) + "\n\n")

    @staticmethod
    def _indented(value: str, *, prefix: str = "  ") -> list[str]:
        return [f"{prefix}{line}" for line in value.splitlines()] or [prefix]

    @staticmethod
    def _display_value(value: Any) -> str:
        if isinstance(value, str):
            return value
        return _OpenCodeRenderer._compact(value)

    @staticmethod
    def _compact(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError, RecursionError):
            return "<structured details unavailable>"

    def _style(self, text: str, code: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self._color else text


def run_harness(
    harness: HarnessSpec,
    prompt: str,
    cwd: Path,
    log_path: Path,
    *,
    on_invocation_starting: Callable[[], None] | None = None,
) -> ProcessOutcome:
    """Start a fresh harness process, retaining raw output and streaming its view."""

    return _run_process(
        harness.command(prompt),
        cwd,
        log_path,
        harness.display_name,
        render_opencode=harness.output_format == "json",
        on_invocation_starting=on_invocation_starting,
    )


def _run_process(
    command: Sequence[str],
    cwd: Path,
    log_path: Path,
    display_name: str,
    *,
    render_opencode: bool = False,
    on_invocation_starting: Callable[[], None] | None = None,
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

    if on_invocation_starting is not None:
        on_invocation_starting()
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
    color = "NO_COLOR" not in environment and sys.stdout.isatty()

    def write_text(value: str) -> None:
        if output is None:
            encoding = sys.stdout.encoding or "utf-8"
            safe_value = value.encode(encoding, errors="replace").decode(encoding, errors="replace")
            sys.stdout.write(safe_value)
            sys.stdout.flush()
        else:
            output.write(value.encode("utf-8", errors="replace"))
            output.flush()

    renderer = _OpenCodeRenderer(write_text, color=color) if render_opencode else None
    try:
        with os.fdopen(descriptor, "wb") as log_file:
            if process.stdout is None:
                log_persisted = False
                log_error = f"{display_name} output stream was unavailable."
            else:
                while True:
                    chunk = os.read(process.stdout.fileno(), 8192)
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
                            if renderer is not None:
                                renderer.feed(chunk)
                            elif output is None:
                                write_text(chunk.decode("utf-8", errors="replace"))
                            else:
                                output.write(chunk)
                                output.flush()
                        except (BrokenPipeError, OSError) as error:
                            stream_error = str(error)
                if renderer is not None and stream_error is None:
                    try:
                        renderer.finish()
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
