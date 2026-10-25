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

        payload = record
        if event_type in {"step_start", "step_finish"}:
            part = payload.get("part")
            expected_part_type = "step-start" if event_type == "step_start" else "step-finish"
            if event_type == "step_start":
                has_terminal_detail = isinstance(part, dict) and (
                    "snapshot" not in part or isinstance(part.get("snapshot"), str)
                )
            else:
                has_terminal_detail = isinstance(part, dict) and (
                    isinstance(part.get("reason"), str)
                    and self._is_timestamp(part.get("cost"))
                    and isinstance(part.get("tokens"), dict)
                    and ("snapshot" not in part or isinstance(part.get("snapshot"), str))
                )
            if (
                self._has_event_envelope(record)
                and isinstance(part, dict)
                and self._has_part_identity(part, expected_part_type, record.get("sessionID"))
                and has_terminal_detail
            ):
                return
            self._render_fallback(f"OpenCode {event_type} event", self._compact(record))
            return
        if event_type == "reasoning":
            part = payload.get("part")
            timing = part.get("time") if isinstance(part, dict) else None
            if (
                self._has_event_envelope(record)
                and isinstance(part, dict)
                and self._has_part_identity(part, "reasoning", record.get("sessionID"))
                and isinstance(part.get("text"), str)
                and isinstance(timing, dict)
                and self._is_timestamp(timing.get("start"))
                and self._is_timestamp(timing.get("end"))
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
        shell_command = self._shell_command(tool_name, input_value)
        if shell_command is not None:
            lines.append("  command:")
            lines.extend(self._indented(shell_command, prefix="    "))
        elif input_value is not None:
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

        metadata = state.get("metadata")
        if self._is_delegated_result(tool_name, input_value, metadata):
            self._render_delegated_result(
                marker,
                marker_color,
                title,
                tool_name,
                input_value,
                metadata,
                output,
            )
            return

        if tool_name.casefold() == "read":
            target = self._input_value(
                input_value, ("filePath", "file_path", "path", "file", "target")
            )
            read_title = title
            if read_title == "Tool result":
                read_title = f"Read {target}" if target is not None else "File read"
            lines = [f"{self._style(marker, marker_color)} {self._style(read_title, '1')}"]
            lines.append(f"  {self._style(f'tool: {tool_name}', '2')}")
            if target is not None:
                lines.append(f"  target: {target}")
            count = self._read_count(metadata)
            if count is not None:
                label, value = count
                lines.append(f"  {label}: {value}")
            self._render_block(lines)
            return

        if self._is_search_tool(tool_name):
            self._render_search(
                marker,
                marker_color,
                title,
                tool_name,
                input_value,
                metadata,
            )
            return

        if output is None or output == "":
            lines.append("  output: (no output)" if shell_command is not None else "  (no output)")
            self._render_block(lines)
            return
        output_lines = self._display_value(output).splitlines()
        if not output_lines:
            lines.append("  output: (no output)" if shell_command is not None else "  (no output)")
        else:
            if shell_command is not None:
                lines.append("  output:")
            lines.extend(f"    {line}" for line in output_lines[:5])
            omitted = len(output_lines) - 5
            if omitted > 0:
                lines.append(f"    ... {omitted} additional line(s) omitted")
        self._render_block(lines)

    @staticmethod
    def _shell_command(tool_name: str, input_value: Any) -> str | None:
        if tool_name != "shell" or not isinstance(input_value, dict):
            return None
        command = input_value.get("command")
        return command if isinstance(command, str) else None

    @staticmethod
    def _input_value(input_value: Any, keys: tuple[str, ...]) -> str | None:
        if not isinstance(input_value, dict):
            return None
        for key in keys:
            value = input_value.get(key)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                rendered = " ".join(str(value).split())
                if rendered:
                    return rendered if len(rendered) <= 240 else rendered[:237] + "..."
        return None

    @staticmethod
    def _read_count(metadata: Any) -> tuple[str, int] | None:
        layers = _OpenCodeRenderer._metadata_layers(metadata)
        if any(
            layer.get("truncated") is True
            or (
                isinstance(layer.get("display"), dict) and layer["display"].get("truncated") is True
            )
            for layer in layers
        ):
            return None
        for layer in layers:
            display = layer.get("display")
            if isinstance(display, dict):
                if display.get("type") == "file":
                    count = display.get("totalLines")
                    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                        return "lines", count
                if display.get("type") == "directory":
                    count = display.get("totalEntries")
                    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                        return "entries", count
            for label, keys in (
                ("lines", ("totalLines", "lineCount")),
                ("entries", ("totalEntries", "entryCount")),
            ):
                for key in keys:
                    count = layer.get(key)
                    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                        return label, count
        return None

    @staticmethod
    def _metadata_layers(metadata: Any) -> list[dict[str, Any]]:
        if not isinstance(metadata, dict):
            return []
        layers = [metadata]
        nested = metadata.get("metadata")
        if isinstance(nested, dict):
            layers.append(nested)
        return layers

    @staticmethod
    def _is_search_tool(tool_name: str) -> bool:
        normalized = tool_name.casefold().replace("-", "_").replace(".", "_")
        parts = set(normalized.split("_"))
        return bool(parts & {"grep", "glob", "search"}) or normalized in {"codesearch", "websearch"}

    @staticmethod
    def _is_delegated_result(tool_name: str, input_value: Any, metadata: Any) -> bool:
        normalized = tool_name.casefold().replace("-", "_").replace(".", "_")
        parts = set(normalized.split("_"))
        if (
            normalized == "task"
            or bool(parts & {"delegate", "delegation", "subagent"})
            or {"agent", "task"}.issubset(parts)
        ):
            return True
        if _OpenCodeRenderer._delegated_agent(input_value, metadata) is not None:
            return True
        return any(
            layer.get("delegated") is True or layer.get("isDelegated") is True
            for layer in _OpenCodeRenderer._metadata_layers(metadata)
        )

    @staticmethod
    def _delegated_agent(input_value: Any, metadata: Any) -> str | None:
        sources = [input_value, *_OpenCodeRenderer._metadata_layers(metadata)]
        keys = (
            "subagent_type",
            "subagentType",
            "agent_type",
            "agentType",
            "agent_name",
            "agentName",
            "delegated_agent",
            "delegatedAgent",
            "delegate_to",
            "delegateTo",
            "subagent",
            "agent",
        )
        for source in sources:
            if not isinstance(source, dict):
                continue
            for key in keys:
                value = source.get(key)
                if isinstance(value, dict):
                    value = _OpenCodeRenderer._input_value(value, ("name", "type", "id"))
                    if value is not None:
                        return value
                elif isinstance(value, (str, int, float)):
                    rendered = _OpenCodeRenderer._input_value({key: value}, (key,))
                    if rendered is not None:
                        return rendered
        return None

    def _render_delegated_result(
        self,
        marker: str,
        marker_color: str,
        title: str,
        tool_name: str,
        input_value: Any,
        metadata: Any,
        output: Any,
    ) -> None:
        description = self._input_value(
            input_value, ("description", "task_description", "taskDescription")
        )
        if description is None and title != "Tool result":
            description = title
        heading = f"Delegated: {description}" if description else "Delegated agent result"
        lines = [f"{self._style(marker, marker_color)} {self._style(heading, '1')}"]
        lines.append(f"  {self._style(f'tool: {tool_name}', '2')}")
        agent = self._delegated_agent(input_value, metadata)
        if agent is not None:
            lines.append(f"  agent: {agent}")
        task_id = self._input_value(input_value, ("task_id", "taskId"))
        if task_id is not None:
            lines.append(f"  task id: {task_id}")
        else:
            session_id = self._metadata_value(metadata, ("sessionId", "sessionID", "session_id"))
            if session_id is not None:
                lines.append(f"  session: {session_id}")

        if output is None or output == "":
            lines.append("  output: (no output)")
        else:
            output_lines = self._display_value(output).splitlines()
            if not output_lines:
                lines.append("  output: (no output)")
            else:
                lines.append("  output:")
                lines.extend(f"    {line}" for line in output_lines[:5])
                omitted = len(output_lines) - 5
                if omitted > 0:
                    lines.append(f"    ... {omitted} additional line(s) omitted")
        self._render_block(lines)

    @staticmethod
    def _metadata_value(metadata: Any, keys: tuple[str, ...]) -> str | None:
        for layer in _OpenCodeRenderer._metadata_layers(metadata):
            value = _OpenCodeRenderer._input_value(layer, keys)
            if value is not None:
                return value
        return None

    def _render_search(
        self,
        marker: str,
        marker_color: str,
        title: str,
        tool_name: str,
        input_value: Any,
        metadata: Any,
    ) -> None:
        if title == "Tool result":
            title = "Search result"
        lines = [f"{self._style(marker, marker_color)} {self._style(title, '1')}"]
        lines.append(f"  {self._style(f'tool: {tool_name}', '2')}")
        query = self._input_value(input_value, ("pattern", "query", "term", "searchTerm"))
        if query is not None:
            lines.append(f"  query: {query}")
        scope = self._input_value(input_value, ("path", "directory", "root", "scope", "url"))
        if scope is not None:
            lines.append(f"  scope: {scope}")
        include = self._input_value(input_value, ("include", "filePattern", "file_pattern"))
        if include is not None:
            lines.append(f"  include: {include}")
        count = self._search_count(metadata)
        if count is not None:
            label, value = count
            lines.append(f"  {label}: {value}")
        self._render_block(lines)

    @staticmethod
    def _search_count(metadata: Any) -> tuple[str, int] | None:
        for layer in _OpenCodeRenderer._metadata_layers(metadata):
            for key in ("matches", "matchCount", "totalMatches"):
                count = layer.get(key)
                if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                    return "matches", count
            for key in ("count", "resultCount", "totalResults", "numResults"):
                count = layer.get(key)
                if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                    return "results", count
        return None

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

    @staticmethod
    def _has_event_envelope(record: dict[str, Any]) -> bool:
        return (
            _OpenCodeRenderer._is_timestamp(record.get("timestamp"))
            and isinstance(record.get("sessionID"), str)
            and bool(record["sessionID"])
        )

    @staticmethod
    def _is_timestamp(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @staticmethod
    def _has_part_identity(
        part: dict[str, Any], expected_type: str, expected_session_id: Any
    ) -> bool:
        return (
            part.get("type") == expected_type
            and part.get("sessionID") == expected_session_id
            and all(
                isinstance(part.get(key), str) and bool(part[key]) for key in ("id", "messageID")
            )
        )


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
