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


@dataclass(frozen=True)
class _FileModificationContract:
    required_strings: tuple[str, ...]
    safe_strings: tuple[str, ...]
    safe_booleans: tuple[str, ...]
    fallback_title: str
    title_prefix: str | None = None


_FILE_MODIFICATION_CONTRACTS = {
    "patch": _FileModificationContract(("patchText",), (), (), "Apply patch"),
    "edit": _FileModificationContract(
        ("path", "oldString", "newString"), ("path",), ("replaceAll",), "Edit file", "Edit"
    ),
    "write": _FileModificationContract(("path", "content"), ("path",), (), "Write file", "Write"),
}


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
        if not text:
            return
        rendered = text
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
        tool_name = (
            " ".join(tool.split()) if isinstance(tool, str) and tool.strip() else "unknown tool"
        )
        input_value = state.get("input", part.get("input", payload.get("input")))
        metadata = state.get("metadata")
        delegated = self._is_delegated_result(tool_name, input_value, metadata)
        title = self._action_title(
            tool_name,
            input_value,
            state.get("title", part.get("title", payload.get("title"))),
            metadata,
        )
        shell_command = self._shell_command(tool_name, input_value)
        output = state.get("output")
        if failed:
            failure_details: list[tuple[str, str]] = []
            if delegated:
                failure_details.append(("└", self._failure_detail(error, output)))
            elif shell_command is not None:
                failure_details.append(("│", shell_command))
            elif input_value is not None:
                failure_details.append(("│", self._input_summary(input_value)))
            if not delegated:
                failure_details.append(("└", self._failure_detail(error, output)))
            self._render_action_block(
                title,
                tool_name,
                failed=True,
                details=failure_details,
                tool_identity=self._delegated_suffix(tool_name, input_value, metadata)
                if delegated
                else None,
            )
            return

        if tool_name.casefold() == "skill":
            skill = self._skill_result(input_value, metadata, output)
            if skill is not None:
                skill_id, _ = skill
                self._render_action_block(
                    title,
                    tool_name,
                    failed=False,
                    details=[("└", f"Loaded {skill_id}")],
                )
                return

        if tool_name.casefold() == "question":
            questions = self._question_prompts(input_value)
            answers = self._question_answers(metadata, len(questions)) if questions else None
            if questions is not None and answers is not None and isinstance(output, str):
                self._render_question_summary(title, tool_name, questions, answers)
                return

        if delegated:
            self._render_delegated_result(
                title,
                tool_name,
                input_value,
                metadata,
                output,
            )
            return

        if tool_name.casefold() == "read" and self._read_input_supported(input_value):
            target = self._read_target(input_value)
            read_details: list[str] = []
            if target is not None and target.casefold() not in title.casefold():
                read_details.append(target)
            count = self._read_count(metadata)
            if count is not None:
                label, value = count
                if label == "lines":
                    description = f"{value} {'line' if value == 1 else 'lines'}"
                else:
                    description = f"{value} {'entry' if value == 1 else 'entries'}"
                read_details.append(description)
            self._render_action_block(
                title, tool_name, failed=False, details=self._tree_details(read_details)
            )
            return

        search_family = self._search_family(tool_name, input_value)
        if search_family is not None:
            self._render_search(title, tool_name, input_value, metadata, search_family)
            return

        if self._is_file_modification_tool(tool_name):
            self._render_file_modification(title, tool_name, input_value, output)
            return

        if tool_name.casefold() == "webfetch":
            self._render_webfetch(title, tool_name, input_value, output)
            return

        action_details: list[tuple[str, str]] = []
        if shell_command is not None:
            action_details.append(("│", shell_command))
        elif input_value is not None:
            action_details.append(("│", self._input_summary(input_value)))
        action_details.extend(self._success_preview(output))
        self._render_action_block(title, tool_name, failed=False, details=action_details)

    def _action_title(self, tool_name: str, input_value: Any, supplied: Any, metadata: Any) -> str:
        if self._is_delegated_result(tool_name, input_value, metadata):
            description = self._input_value(
                input_value, ("description", "task_description", "taskDescription")
            )
            if description is not None:
                return description
        if isinstance(supplied, str):
            title = " ".join(supplied.split())
            if title and title.casefold() != tool_name.casefold():
                return title

        normalized_tool = tool_name.casefold()
        if normalized_tool == "shell":
            return "Run command"
        if normalized_tool == "skill":
            skill_id = self._skill_id(input_value)
            skill_metadata = self._skill_metadata(metadata)
            if skill_id is not None and skill_metadata is not None:
                skill_name = skill_metadata[0]
                return f"Load {skill_name} instructions"
        if normalized_tool == "question":
            questions = self._question_prompts(input_value)
            answers = self._question_answers(metadata, len(questions)) if questions else None
            if questions is not None and answers is not None:
                if len(questions) == 1:
                    return self._bounded_single_line(questions[0]["question"]) or "Answer question"
                return f"Answer {len(questions)} questions"
        if normalized_tool == "read":
            target = (
                self._read_target(input_value) if self._read_input_supported(input_value) else None
            )
            return f"Read {target}" if target is not None else "Read file"
        modification = _FILE_MODIFICATION_CONTRACTS.get(normalized_tool)
        if modification is not None:
            target = self._input_string(input_value, "path")
            if modification.title_prefix is not None and target is not None:
                return f"{modification.title_prefix} {target}"
            return modification.fallback_title
        if normalized_tool == "webfetch":
            return "Fetch web content"
        search_family = self._search_family(tool_name, input_value)
        if search_family == "grep":
            return "Search text"
        if search_family == "glob":
            return "Find files"
        if search_family == "websearch":
            return "Search the web"
        if self._is_delegated_result(tool_name, input_value, metadata):
            return "Run delegated task"
        return "Use tool"

    def _render_action_block(
        self,
        title: str,
        tool_name: str,
        *,
        failed: bool,
        details: list[tuple[str, str]],
        tool_identity: str | None = None,
    ) -> None:
        marker = "✗" if failed else "✓"
        marker_color = "31" if failed else "32"
        lines = [
            f"{self._style(marker, marker_color)} {self._style(title, '1')} "
            f"{self._style(f'({tool_identity or tool_name})', '2')}"
        ]
        for connector, detail in details:
            detail_lines = detail.splitlines() or [""]
            lines.append(f"  {self._style(connector, '2')} {detail_lines[0]}")
            lines.extend(f"    {line}" for line in detail_lines[1:])
        self._render_block(lines)

    @staticmethod
    def _tree_details(values: list[str]) -> list[tuple[str, str]]:
        return [
            ("└" if index == len(values) - 1 else "├", value) for index, value in enumerate(values)
        ]

    def _input_summary(self, input_value: Any) -> str:
        summary = self._compact(input_value)
        return summary if len(summary) <= 240 else summary[:237] + "..."

    @staticmethod
    def _skill_id(input_value: Any) -> str | None:
        if not isinstance(input_value, dict) or set(input_value) != {"id"}:
            return None
        value = input_value.get("id")
        if not isinstance(value, str) or not value or value != value.strip() or len(value) > 240:
            return None
        if any(character.isspace() for character in value):
            return None
        return value

    @staticmethod
    def _skill_metadata(metadata: Any) -> tuple[str, str] | None:
        for layer in reversed(_OpenCodeRenderer._metadata_layers(metadata)):
            if "name" not in layer and "directory" not in layer:
                continue
            name = layer.get("name")
            directory = layer.get("directory")
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(directory, str)
                or not directory.strip()
            ):
                return None
            display_name = _OpenCodeRenderer._bounded_single_line(name)
            if display_name is None:
                return None
            return display_name, directory
        return None

    @staticmethod
    def _skill_result(input_value: Any, metadata: Any, output: Any) -> tuple[str, str] | None:
        skill_id = _OpenCodeRenderer._skill_id(input_value)
        skill_metadata = _OpenCodeRenderer._skill_metadata(metadata)
        if skill_id is None or skill_metadata is None or not isinstance(output, str):
            return None
        return skill_id, skill_metadata[0]

    @staticmethod
    def _question_prompts(input_value: Any) -> list[dict[str, Any]] | None:
        if not isinstance(input_value, dict) or set(input_value) != {"questions"}:
            return None
        questions = input_value.get("questions")
        if not isinstance(questions, list) or not questions:
            return None

        supported: list[dict[str, Any]] = []
        for question in questions:
            required = {"question", "header", "options"}
            allowed = required | {"multiple"}
            if (
                not isinstance(question, dict)
                or required - set(question)
                or set(question) - allowed
            ):
                return None
            if (
                not isinstance(question.get("question"), str)
                or not question["question"].strip()
                or not isinstance(question.get("header"), str)
                or not question["header"].strip()
                or not isinstance(question.get("options"), list)
                or ("multiple" in question and not isinstance(question["multiple"], bool))
            ):
                return None
            for option in question["options"]:
                if (
                    not isinstance(option, dict)
                    or set(option) != {"label", "description"}
                    or not isinstance(option.get("label"), str)
                    or not isinstance(option.get("description"), str)
                ):
                    return None
            supported.append(question)
        return supported

    @staticmethod
    def _question_answers(metadata: Any, question_count: int) -> list[list[str]] | None:
        for layer in reversed(_OpenCodeRenderer._metadata_layers(metadata)):
            if "answers" not in layer:
                continue
            answers = layer.get("answers")
            if (
                not isinstance(answers, list)
                or len(answers) != question_count
                or any(
                    not isinstance(answer, list)
                    or any(not isinstance(value, str) for value in answer)
                    for answer in answers
                )
            ):
                return None
            return answers
        return None

    def _render_question_summary(
        self,
        title: str,
        tool_name: str,
        questions: list[dict[str, Any]],
        answers: list[list[str]],
    ) -> None:
        details: list[tuple[str, str]] = []
        if len(questions) == 1:
            question = questions[0]
            prompt = self._bounded_single_line(question["question"])
            if prompt is not None and title.casefold() != prompt.casefold():
                details.append(("├", f"{question['header']}: {prompt}"))
            details.append(("└", self._question_answer(question, answers[0])))
        else:
            for index, (question, answer) in enumerate(zip(questions, answers, strict=True)):
                prompt = self._bounded_single_line(question["question"])
                if prompt is None:
                    prompt = question["header"]
                label = question["header"]
                if label.casefold() != prompt.casefold():
                    label = f"{label}: {prompt}"
                details.append(
                    (
                        "└" if index == len(questions) - 1 else "├",
                        f"{label}\n{self._question_answer(question, answer)}",
                    )
                )
        self._render_action_block(title, tool_name, failed=False, details=details)

    @staticmethod
    def _question_answer(question: dict[str, Any], answers: list[str]) -> str:
        if not answers:
            return "No answer supplied"
        answer = ", ".join(answers)
        option_labels = {option["label"] for option in question["options"]}
        if all(value in option_labels for value in answers):
            return f"Selected {answer}"
        return answer

    @staticmethod
    def _bounded_single_line(value: str) -> str | None:
        rendered = " ".join(value.split())
        if not rendered:
            return None
        return rendered if len(rendered) <= 240 else rendered[:237] + "..."

    @staticmethod
    def _input_string(input_value: Any, key: str) -> str | None:
        if not isinstance(input_value, dict) or not isinstance(input_value.get(key), str):
            return None
        return _OpenCodeRenderer._bounded_single_line(input_value[key])

    @staticmethod
    def _read_input_supported(input_value: Any) -> bool:
        if not isinstance(input_value, dict):
            return False
        if "path" not in input_value or set(input_value) - {"path", "offset", "limit"}:
            return False
        if not isinstance(input_value["path"], str) or not input_value["path"].strip():
            return False
        return all(
            key not in input_value or _OpenCodeRenderer._count_value(input_value[key]) is not None
            for key in ("offset", "limit")
        )

    @staticmethod
    def _read_target(input_value: Any) -> str | None:
        if not _OpenCodeRenderer._read_input_supported(input_value):
            return None
        assert isinstance(input_value, dict)
        return _OpenCodeRenderer._bounded_single_line(input_value["path"])

    def _failure_detail(self, error: Any, output: Any) -> str:
        error_text = self._display_value(error) if error is not None else ""
        if not error_text:
            error_text = "No error details available."
        if not error_text.casefold().startswith("error:"):
            error_text = f"Error: {error_text}"
        output_text = self._display_value(output) if output is not None else ""
        return f"{error_text}\n{output_text}" if output_text else error_text

    def _success_preview(self, output: Any) -> list[tuple[str, str]]:
        if output is None or output == "":
            return [("└", "No output")]
        output_lines = self._display_value(output).splitlines()
        if not output_lines or not any(output_lines):
            return [("└", "No output")]
        preview = "\n".join(output_lines[:5])
        omitted = len(output_lines) - 5
        if omitted > 0:
            return [
                ("├", preview),
                ("└", f"... {omitted} additional line(s) omitted"),
            ]
        return [("└", preview)]

    def _render_file_modification(
        self, title: str, tool_name: str, input_value: Any, output: Any
    ) -> None:
        details: list[tuple[str, str]] = []
        if not self._has_file_modification_input(tool_name, input_value):
            input_summary = self._file_modification_input_summary(tool_name, input_value)
            if input_summary is not None:
                details.append(("│", input_summary))
        details.extend(self._file_modification_preview(tool_name, output))
        self._render_action_block(title, tool_name, failed=False, details=details)

    @staticmethod
    def _is_file_modification_tool(tool_name: str) -> bool:
        return tool_name.casefold() in _FILE_MODIFICATION_CONTRACTS

    @staticmethod
    def _has_file_modification_input(tool_name: str, input_value: Any) -> bool:
        if not isinstance(input_value, dict):
            return False
        contract = _FILE_MODIFICATION_CONTRACTS.get(tool_name.casefold())
        if contract is None or not all(
            isinstance(input_value.get(key), str) for key in contract.required_strings
        ):
            return False
        return all(
            key not in input_value or isinstance(input_value[key], bool)
            for key in contract.safe_booleans
        )

    def _file_modification_input_summary(self, tool_name: str, input_value: Any) -> str | None:
        contract = _FILE_MODIFICATION_CONTRACTS.get(tool_name.casefold())
        if contract is None or not isinstance(input_value, dict):
            return None
        safe_input: dict[str, Any] = {}
        safe_input.update(
            {
                key: input_value[key]
                for key in contract.safe_strings
                if isinstance(input_value.get(key), str)
            }
        )
        safe_input.update(
            {
                key: input_value[key]
                for key in contract.safe_booleans
                if isinstance(input_value.get(key), bool)
            }
        )
        return self._input_summary(safe_input) if safe_input else None

    def _file_modification_preview(self, tool_name: str, output: Any) -> list[tuple[str, str]]:
        if tool_name.casefold() == "patch" and isinstance(output, str):
            lines = output.splitlines()
            if lines and lines[0] == "Success. Updated the following files:":
                output = "\n".join(lines[1:])
        return self._success_preview(output)

    def _render_webfetch(self, title: str, tool_name: str, input_value: Any, output: Any) -> None:
        details: list[tuple[str, str]] = []
        url = self._input_string(input_value, "url")
        if url is not None:
            details.append(("│", url))
        elif input_value is not None:
            details.append(("│", self._input_summary(input_value)))
        details.extend(self._success_preview(output))
        self._render_action_block(title, tool_name, failed=False, details=details)

    @staticmethod
    def _shell_command(tool_name: str, input_value: Any) -> str | None:
        if tool_name.casefold() != "shell" or not isinstance(input_value, dict):
            return None
        command = input_value.get("command")
        return command if isinstance(command, str) else None

    @staticmethod
    def _input_value(input_value: Any, keys: tuple[str, ...]) -> str | None:
        if not isinstance(input_value, dict):
            return None
        for key in keys:
            value = input_value.get(key)
            if isinstance(value, str):
                rendered = _OpenCodeRenderer._bounded_single_line(value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                rendered = _OpenCodeRenderer._bounded_single_line(str(value))
            else:
                rendered = None
            if rendered is not None:
                return rendered
        return None

    @staticmethod
    def _count_value(value: Any) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
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
            display_layer = display if isinstance(display, dict) else {}
            display_type = display_layer.get("type")
            if display_type == "media":
                continue
            if display_type == "file":
                for source in (display_layer, layer):
                    for key in ("totalLines", "lineCount"):
                        count = _OpenCodeRenderer._count_value(source.get(key))
                        if count is not None:
                            return "lines", count
                continue
            if display_type == "directory":
                for source in (display_layer, layer):
                    for key in ("totalEntries", "entryCount"):
                        count = _OpenCodeRenderer._count_value(source.get(key))
                        if count is not None:
                            return "entries", count
                continue
            for label, keys in (
                ("lines", ("totalLines", "lineCount")),
                ("entries", ("totalEntries", "entryCount")),
            ):
                for key in keys:
                    count = _OpenCodeRenderer._count_value(layer.get(key))
                    if count is not None:
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
    def _search_family(tool_name: str, input_value: Any) -> str | None:
        if not isinstance(input_value, dict):
            return None

        normalized = tool_name.casefold()
        if normalized == "grep":
            allowed = {"pattern", "path", "include", "literal", "caseSensitive", "limit"}
            if (
                set(input_value) - allowed
                or not isinstance(input_value.get("pattern"), str)
                or not input_value["pattern"].strip()
                or ("path" in input_value and not isinstance(input_value["path"], str))
                or ("include" in input_value and not isinstance(input_value["include"], str))
                or ("literal" in input_value and not isinstance(input_value["literal"], bool))
                or (
                    "caseSensitive" in input_value
                    and not isinstance(input_value["caseSensitive"], bool)
                )
                or (
                    "limit" in input_value
                    and _OpenCodeRenderer._count_value(input_value["limit"]) is None
                )
            ):
                return None
            return "grep"

        if normalized == "glob":
            allowed = {"pattern", "path", "hidden", "limit"}
            if (
                set(input_value) - allowed
                or not isinstance(input_value.get("pattern"), str)
                or not input_value["pattern"].strip()
                or ("path" in input_value and not isinstance(input_value["path"], str))
                or ("hidden" in input_value and not isinstance(input_value["hidden"], bool))
                or (
                    "limit" in input_value
                    and _OpenCodeRenderer._count_value(input_value["limit"]) is None
                )
            ):
                return None
            return "glob"

        if normalized == "websearch":
            if set(input_value) != {"query"}:
                return None
            if not isinstance(input_value.get("query"), str) or not input_value["query"].strip():
                return None
            return "websearch"

        return None

    @staticmethod
    def _is_delegated_result(tool_name: str, input_value: Any, metadata: Any) -> bool:
        if any(
            layer.get("delegated") is True or layer.get("isDelegated") is True
            for layer in _OpenCodeRenderer._metadata_layers(metadata)
        ):
            return True
        if not isinstance(input_value, dict):
            return False

        normalized = tool_name.casefold()
        if normalized == "subagent":
            allowed = {"agent", "description", "prompt", "model", "sessionID", "background"}
            return (
                not set(input_value) - allowed
                and isinstance(input_value.get("description"), str)
                and isinstance(input_value.get("prompt"), str)
                and ("agent" not in input_value or isinstance(input_value["agent"], str))
                and ("model" not in input_value or isinstance(input_value["model"], str))
                and ("sessionID" not in input_value or isinstance(input_value["sessionID"], str))
                and ("background" not in input_value or isinstance(input_value["background"], bool))
            )

        if normalized == "task":
            allowed = {
                "description",
                "prompt",
                "subagent_type",
                "subagentType",
                "agent",
                "task_id",
                "taskId",
            }
            string_fields = allowed
            return (
                not set(input_value) - allowed
                and all(
                    isinstance(input_value[key], str) for key in string_fields if key in input_value
                )
                and any(
                    isinstance(input_value.get(key), str) and bool(input_value[key].strip())
                    for key in ("description", "prompt", "subagent_type", "subagentType", "agent")
                )
            )

        return False

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
                    value = next(
                        (
                            value[candidate]
                            for candidate in ("name", "type", "id")
                            if isinstance(value.get(candidate), str)
                        ),
                        None,
                    )
                if isinstance(value, str):
                    rendered = _OpenCodeRenderer._bounded_single_line(value)
                    if rendered is not None:
                        return rendered
        return None

    def _delegated_suffix(self, tool_name: str, input_value: Any, metadata: Any) -> str:
        agent = self._delegated_agent(input_value, metadata)
        return f"{tool_name}: {agent}" if agent is not None else tool_name

    def _render_delegated_result(
        self,
        title: str,
        tool_name: str,
        input_value: Any,
        metadata: Any,
        output: Any,
    ) -> None:
        description = self._input_value(
            input_value, ("description", "task_description", "taskDescription")
        )
        heading = description or title
        details = self._success_preview(self._delegated_output(tool_name, output, input_value))
        self._render_action_block(
            heading,
            tool_name,
            failed=False,
            details=details,
            tool_identity=self._delegated_suffix(tool_name, input_value, metadata),
        )

    def _delegated_output(self, tool_name: str, output: Any, input_value: Any) -> Any:
        if not isinstance(output, str):
            return output

        normalized = tool_name.casefold()
        if normalized == "subagent":
            opening, separator, content = output.partition("\n")
            closing = "\n</subagent>"
            if (
                separator
                and opening.startswith('<subagent sessionID="')
                and opening.endswith(' state="completed">')
                and content.endswith(closing)
            ):
                return content[: -len(closing)]

        if normalized == "task":
            content = output.strip()
            opening = "<task_result>"
            closing = "</task_result>"
            first_line, separator, remainder = content.partition("\n")
            remainder = remainder.strip()
            task_id = self._input_value(input_value, ("task_id", "taskId"))
            if (
                first_line.startswith("task_id:")
                and separator
                and (
                    (remainder.startswith(opening) and remainder.endswith(closing))
                    or first_line == f"task_id: {task_id}"
                )
            ):
                content = remainder
            if content.startswith(opening) and content.endswith(closing):
                return content[len(opening) : -len(closing)].strip()
            return content

        return output

    def _render_search(
        self,
        title: str,
        tool_name: str,
        input_value: Any,
        metadata: Any,
        family: str,
    ) -> None:
        details: list[str] = []
        if family == "grep":
            pattern = self._input_string(input_value, "pattern")
            if pattern is not None:
                details.append(f"for {pattern}")
            scope = self._input_string(input_value, "path")
            if scope is not None:
                details.append(f"in {scope}")
            include = self._input_string(input_value, "include")
            if include is not None:
                details.append(f"files matching {include}")
            if input_value.get("literal") is True:
                details.append("literal text")
            if input_value.get("caseSensitive") is False:
                details.append("case-insensitive")
            limit = self._count_value(input_value.get("limit"))
            if limit is not None:
                details.append(f"up to {limit} matches")
        elif family == "glob":
            pattern = self._input_string(input_value, "pattern")
            if pattern is not None:
                details.append(pattern)
            scope = self._input_string(input_value, "path")
            if scope is not None:
                details.append(f"in {scope}")
            if input_value.get("hidden") is True:
                details.append("including hidden files")
            limit = self._count_value(input_value.get("limit"))
            if limit is not None:
                details.append(f"up to {limit} files")
        else:
            query = self._input_string(input_value, "query")
            if query is not None:
                details.append(f"for {query}")

        count = self._search_count(metadata, family)
        if count is not None:
            value, truncated = count
            if family == "glob":
                quantity = "file" if value == 1 else "files"
            else:
                quantity = "match" if value == 1 else "matches"
            description = f"more than {value} {quantity}" if truncated else f"{value} {quantity}"
            details.append(description)
        self._render_action_block(
            title, tool_name, failed=False, details=self._tree_details(details)
        )

    @staticmethod
    def _search_count(metadata: Any, family: str) -> tuple[int, bool] | None:
        key = {"grep": "matches", "glob": "count"}.get(family)
        if key is None:
            return None
        for layer in _OpenCodeRenderer._metadata_layers(metadata):
            count = _OpenCodeRenderer._count_value(layer.get(key))
            if count is not None:
                truncated = any(
                    item.get("truncated") is True
                    for item in _OpenCodeRenderer._metadata_layers(metadata)
                )
                return count, truncated
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
