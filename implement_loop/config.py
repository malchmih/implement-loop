"""Command-line and user TOML configuration for the ticket runner."""

from __future__ import annotations

import argparse
import os
import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .harness import HarnessSettings, default_harness_settings
from .records import RunRecordSettings

_POSITIVE_INTEGER = re.compile(r"^[1-9][0-9]{0,8}$")
_NONNEGATIVE_INTEGER = re.compile(r"^(0|[1-9][0-9]{0,8})$")
_COPILOT_REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
_MAX_INTEGER = 999_999_999
_DEFAULT_MAX_ITERATIONS = 5
_DEFAULT_RETENTION_DAYS = 7


@dataclass(frozen=True)
class Options:
    """Fully resolved options accepted by the public executable."""

    spec_number: int
    max_iterations: int
    allow_no_new_commit_ticket: int | None
    harness: str
    opencode: HarnessSettings
    copilot: HarnessSettings
    records: RunRecordSettings
    config_file: Path | None


class _ConfigurationError(ValueError):
    """A user configuration cannot be applied safely."""


@dataclass(frozen=True)
class _RecordPreferences:
    directory: Path | None
    retention_days: int | None


@dataclass(frozen=True)
class _Preferences:
    """Configuration values before the positional spec number is attached."""

    harness: str
    max_iterations: int
    opencode: HarnessSettings
    copilot: HarnessSettings
    records: _RecordPreferences


def _positive_integer(value: str) -> int:
    if not _POSITIVE_INTEGER.fullmatch(value):
        raise argparse.ArgumentTypeError("must be a positive integer with at most 9 digits")
    return int(value)


def _nonnegative_integer(value: str) -> int:
    if not _NONNEGATIVE_INTEGER.fullmatch(value):
        raise argparse.ArgumentTypeError("must be a non-negative integer with at most 9 digits")
    return int(value)


def _nonempty_setting(value: str) -> str:
    if (
        not value
        or value.isspace()
        or "\x00" in value
        or any(character.isspace() for character in value)
    ):
        raise argparse.ArgumentTypeError("must be a non-empty value without whitespace or NUL")
    return value


def _reasoning_effort_argument(value: str) -> str:
    if value not in _COPILOT_REASONING_EFFORTS:
        choices = ", ".join(_COPILOT_REASONING_EFFORTS)
        raise argparse.ArgumentTypeError(f"reasoning effort must be one of: {choices}")
    return value


def _path_argument(value: str) -> str:
    if not value.strip() or "\x00" in value:
        raise argparse.ArgumentTypeError("must be a non-empty path")
    return value


def _absolute_xdg_directory(environment_name: str, value: str) -> Path:
    try:
        directory = Path(value).expanduser()
    except (RuntimeError, ValueError) as error:
        raise _ConfigurationError(f"{environment_name} is not a valid path: {error}") from error
    if not directory.is_absolute():
        raise _ConfigurationError(f"{environment_name} must be an absolute path.")
    return _resolve_path(directory, environment_name)


def _resolve_path(value: str | Path, setting: str) -> Path:
    try:
        path = Path(value).expanduser()
        return path.resolve()
    except (OSError, RuntimeError, ValueError) as error:
        raise _ConfigurationError(f"{setting} is not a usable path: {error}") from error


def _default_config_path() -> Path:
    config_home_value = os.environ.get("XDG_CONFIG_HOME")
    if config_home_value:
        config_home = _absolute_xdg_directory("XDG_CONFIG_HOME", config_home_value)
    else:
        config_home = Path.home() / ".config"
    return config_home / "implement-loop" / "config.toml"


def _read_config_file(path: Path, *, explicit: bool) -> dict[str, Any] | None:
    if not explicit and not os.path.lexists(path):
        return None
    try:
        with path.open("rb") as config_file:
            document = tomllib.load(config_file)
    except tomllib.TOMLDecodeError as error:
        raise _ConfigurationError(f"Invalid TOML in {path}: {error}") from error
    except UnicodeDecodeError as error:
        raise _ConfigurationError(
            f"Configuration file {path} is not valid UTF-8: {error}"
        ) from error
    except OSError as error:
        selection = "explicitly selected" if explicit else "discovered"
        raise _ConfigurationError(
            f"Cannot read {selection} configuration file {path}: {error}"
        ) from error
    if not isinstance(document, dict):
        raise _ConfigurationError(f"Configuration file {path} must contain a TOML table.")
    return document


def _selected_config_file(raw_path: str | None) -> tuple[Path | None, dict[str, Any]]:
    if raw_path is None:
        path = _default_config_path()
        document = _read_config_file(path, explicit=False)
        if document is None:
            return None, {}
        return path, document

    path = _resolve_path(raw_path, "--config-file")
    document = _read_config_file(path, explicit=True)
    assert document is not None
    return path, document


def _table(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _ConfigurationError(f"{context} must be a TOML table.")
    return value


def _section(table: dict[str, Any], key: str, context: str) -> dict[str, Any]:
    value = table.get(key, {})
    return _table(value, f"{context}.{key}")


def _reject_unknown_keys(table: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        allowed_list = ", ".join(sorted(allowed))
        raise _ConfigurationError(
            f"Unknown configuration key {context}.{unknown[0]}. Supported keys: {allowed_list}."
        )


def _config_setting_string(value: Any, context: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.isspace()
        or "\x00" in value
        or any(character.isspace() for character in value)
    ):
        raise _ConfigurationError(
            f"{context} must be a non-empty string without whitespace or NUL."
        )
    return value


def _config_reasoning_effort(value: Any, context: str) -> str:
    effort = _config_setting_string(value, context)
    if effort not in _COPILOT_REASONING_EFFORTS:
        choices = ", ".join(_COPILOT_REASONING_EFFORTS)
        raise _ConfigurationError(f"{context} must be one of: {choices}.")
    return effort


def _config_integer(value: Any, context: str, *, allow_zero: bool) -> int:
    minimum = 0 if allow_zero else 1
    if type(value) is not int or not minimum <= value <= _MAX_INTEGER:
        qualifier = "non-negative" if allow_zero else "positive"
        raise _ConfigurationError(f"{context} must be a {qualifier} integer with at most 9 digits.")
    return value


def _config_path(value: Any, context: str) -> Path:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise _ConfigurationError(f"{context} must be a non-empty path string.")
    return _resolve_path(value, context)


def _apply_harness_table(
    name: str, settings: HarnessSettings, table: dict[str, Any]
) -> HarnessSettings:
    if name == "opencode":
        _reject_unknown_keys(table, {"model", "variant"}, f"execution.harnesses.{name}")
        model = (
            _config_setting_string(table["model"], f"execution.harnesses.{name}.model")
            if "model" in table
            else settings.model
        )
        variant = (
            _config_setting_string(table["variant"], f"execution.harnesses.{name}.variant")
            if "variant" in table
            else settings.model_variant
        )
        return replace(settings, model=model, model_variant=variant)

    _reject_unknown_keys(
        table,
        {"model", "reasoning_effort"},
        f"execution.harnesses.{name}",
    )
    model = (
        _config_setting_string(table["model"], f"execution.harnesses.{name}.model")
        if "model" in table
        else settings.model
    )
    reasoning_effort = (
        _config_reasoning_effort(
            table["reasoning_effort"],
            f"execution.harnesses.{name}.reasoning_effort",
        )
        if "reasoning_effort" in table
        else settings.reasoning_effort
    )
    return replace(settings, model=model, reasoning_effort=reasoning_effort)


def _apply_config(document: dict[str, Any], preferences: _Preferences) -> _Preferences:
    _reject_unknown_keys(document, {"execution", "records"}, "root")
    execution = _section(document, "execution", "root")
    _reject_unknown_keys(execution, {"harness", "max_attempts", "harnesses"}, "execution")

    if "harness" in execution:
        configured_harness = execution["harness"]
        if configured_harness not in ("opencode", "copilot"):
            raise _ConfigurationError("execution.harness must be either 'opencode' or 'copilot'.")
        preferences = replace(preferences, harness=configured_harness)
    if "max_attempts" in execution:
        preferences = replace(
            preferences,
            max_iterations=_config_integer(
                execution["max_attempts"], "execution.max_attempts", allow_zero=False
            ),
        )

    harnesses = _section(execution, "harnesses", "execution")
    _reject_unknown_keys(harnesses, {"opencode", "copilot"}, "execution.harnesses")
    if "opencode" in harnesses:
        preferences = replace(
            preferences,
            opencode=_apply_harness_table(
                "opencode",
                preferences.opencode,
                _table(harnesses["opencode"], "execution.harnesses.opencode"),
            ),
        )
    if "copilot" in harnesses:
        preferences = replace(
            preferences,
            copilot=_apply_harness_table(
                "copilot",
                preferences.copilot,
                _table(harnesses["copilot"], "execution.harnesses.copilot"),
            ),
        )

    records = _section(document, "records", "root")
    _reject_unknown_keys(
        records,
        {"directory", "retention_days", "keep_forever"},
        "records",
    )
    record_preferences = preferences.records
    if "directory" in records:
        record_preferences = replace(
            record_preferences,
            directory=_config_path(records["directory"], "records.directory"),
        )
    if "retention_days" in records:
        record_preferences = replace(
            record_preferences,
            retention_days=_config_integer(
                records["retention_days"], "records.retention_days", allow_zero=True
            ),
        )
    if "keep_forever" in records:
        keep_forever = records["keep_forever"]
        if type(keep_forever) is not bool:
            raise _ConfigurationError("records.keep_forever must be true or false.")
        if keep_forever:
            record_preferences = replace(record_preferences, retention_days=None)

    return replace(preferences, records=record_preferences)


def _default_preferences() -> _Preferences:
    return _Preferences(
        harness="opencode",
        max_iterations=_DEFAULT_MAX_ITERATIONS,
        opencode=default_harness_settings("opencode"),
        copilot=default_harness_settings("copilot"),
        records=_RecordPreferences(directory=None, retention_days=_DEFAULT_RETENTION_DAYS),
    )


def _default_records_dir() -> Path:
    state_home_value = os.environ.get("XDG_STATE_HOME")
    if state_home_value:
        state_home = _absolute_xdg_directory("XDG_STATE_HOME", state_home_value)
    else:
        state_home = Path.home() / ".local" / "state"
    return (state_home / "implement-loop").resolve()


def parse_args(argv: Sequence[str] | None = None) -> Options:
    """Parse the compact invocation and apply defaults, user config, then CLI values."""

    parser = argparse.ArgumentParser(
        prog="implement-loop",
        description=("Implement eligible, prepared direct child issues of a GitHub spec."),
        epilog=(
            "Each selected-harness invocation is one attempt. The loop stops successfully "
            "when no eligible child remains. User settings are read from "
            "${XDG_CONFIG_HOME:-~/.config}/implement-loop/config.toml."
        ),
    )
    parser.add_argument("spec_number", metavar="SPEC_NUMBER", type=_positive_integer)
    parser.add_argument(
        "--config-file",
        metavar="PATH",
        type=_path_argument,
        help="use this TOML file instead of the discovered user configuration",
    )
    parser.add_argument(
        "--harness",
        choices=("opencode", "copilot"),
        default=None,
        help="agent CLI to run (default: opencode)",
    )
    parser.add_argument(
        "--max-iterations",
        metavar="N",
        type=_positive_integer,
        default=None,
        help="maximum selected-harness invocations (default: 5)",
    )
    parser.add_argument(
        "--allow-no-new-commit",
        metavar="TICKET_NUMBER",
        type=_positive_integer,
        help="waive the new-commit requirement for this ticket in this invocation only",
    )
    parser.add_argument(
        "--opencode-model",
        metavar="MODEL",
        type=_nonempty_setting,
        help="OpenCode model name",
    )
    parser.add_argument(
        "--opencode-variant",
        metavar="VARIANT",
        type=_nonempty_setting,
        help="OpenCode model variant (default: max)",
    )
    parser.add_argument(
        "--copilot-model",
        metavar="MODEL",
        type=_nonempty_setting,
        help="Copilot model name",
    )
    parser.add_argument(
        "--copilot-reasoning-effort",
        metavar="EFFORT",
        type=_reasoning_effort_argument,
        help="Copilot reasoning effort (default: max)",
    )
    parser.add_argument(
        "--records-dir",
        metavar="PATH",
        type=_path_argument,
        help="directory for private loop-owned run records",
    )
    retention = parser.add_mutually_exclusive_group()
    retention.add_argument(
        "--retention-days",
        metavar="N",
        type=_nonnegative_integer,
        help="remove unmarked loop records older than N days at startup (default: 7)",
    )
    retention.add_argument(
        "--keep-records-forever",
        action="store_true",
        default=None,
        help="disable startup expiry of loop-owned run records",
    )
    parsed = parser.parse_args(argv)

    try:
        config_file, document = _selected_config_file(parsed.config_file)
        preferences = _apply_config(document, _default_preferences())

        if parsed.harness is not None:
            preferences = replace(preferences, harness=parsed.harness)
        if parsed.max_iterations is not None:
            preferences = replace(preferences, max_iterations=parsed.max_iterations)
        if parsed.opencode_model is not None:
            preferences = replace(
                preferences,
                opencode=replace(preferences.opencode, model=parsed.opencode_model),
            )
        if parsed.opencode_variant is not None:
            preferences = replace(
                preferences,
                opencode=replace(preferences.opencode, model_variant=parsed.opencode_variant),
            )
        if parsed.copilot_model is not None:
            preferences = replace(
                preferences,
                copilot=replace(preferences.copilot, model=parsed.copilot_model),
            )
        if parsed.copilot_reasoning_effort is not None:
            preferences = replace(
                preferences,
                copilot=replace(
                    preferences.copilot,
                    reasoning_effort=parsed.copilot_reasoning_effort,
                ),
            )

        if parsed.records_dir is not None:
            records_directory = _resolve_path(parsed.records_dir, "--records-dir")
        elif preferences.records.directory is not None:
            records_directory = preferences.records.directory
        else:
            records_directory = _default_records_dir()

        retention_days = preferences.records.retention_days
        if parsed.retention_days is not None:
            retention_days = parsed.retention_days
        elif parsed.keep_records_forever:
            retention_days = None
        records = RunRecordSettings(
            directory=records_directory,
            retention_days=retention_days,
        )
    except _ConfigurationError as error:
        parser.error(str(error))

    return Options(
        spec_number=parsed.spec_number,
        max_iterations=preferences.max_iterations,
        allow_no_new_commit_ticket=parsed.allow_no_new_commit,
        harness=preferences.harness,
        opencode=preferences.opencode,
        copilot=preferences.copilot,
        records=records,
        config_file=config_file,
    )
