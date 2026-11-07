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

from .harness import HarnessSettings, default_opencode_settings
from .records import RunRecordSettings

_POSITIVE_INTEGER = re.compile(r"^[1-9][0-9]{0,8}$")
_DEFAULT_MAX_ITERATIONS = 10


@dataclass(frozen=True)
class Options:
    """Fully resolved options accepted by the public executable."""

    spec_number: int
    max_iterations: int
    opencode: HarnessSettings
    records: RunRecordSettings
    config_file: Path | None


class _ConfigurationError(ValueError):
    """A user configuration cannot be applied safely."""


def _positive_integer(value: str) -> int:
    if not _POSITIVE_INTEGER.fullmatch(value):
        raise argparse.ArgumentTypeError("must be a positive integer with at most 9 digits")
    return int(value)


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


def _read_config_file(path: Path) -> dict[str, Any] | None:
    if not os.path.lexists(path):
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
        raise _ConfigurationError(
            f"Cannot read discovered configuration file {path}: {error}"
        ) from error
    return document


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


def _opencode_settings(document: dict[str, Any] | None) -> HarnessSettings:
    settings = default_opencode_settings()
    if document is None:
        return settings

    _reject_unknown_keys(document, {"model", "variant"}, "root")
    model = (
        _config_setting_string(document["model"], "model")
        if "model" in document
        else settings.model
    )
    variant = (
        _config_setting_string(document["variant"], "variant")
        if "variant" in document
        else settings.model_variant
    )
    return replace(settings, model=model, model_variant=variant)


def _default_records_dir() -> Path:
    state_home_value = os.environ.get("XDG_STATE_HOME")
    if state_home_value:
        state_home = _absolute_xdg_directory("XDG_STATE_HOME", state_home_value)
    else:
        state_home = Path.home() / ".local" / "state"
    return (state_home / "implement-loop").resolve()


def parse_args(argv: Sequence[str] | None = None) -> Options:
    """Parse the invocation and apply built-in and optional user OpenCode settings."""

    parser = argparse.ArgumentParser(
        prog="implement-loop",
        description=("Implement eligible, prepared direct child issues of a GitHub spec."),
        epilog=(
            "The optional user settings file is read from "
            "${XDG_CONFIG_HOME:-~/.config}/implement-loop/config.toml."
        ),
    )
    parser.add_argument("spec_number", metavar="SPEC_NUMBER", type=_positive_integer)
    parser.add_argument(
        "--max-iterations",
        metavar="N",
        type=_positive_integer,
        default=_DEFAULT_MAX_ITERATIONS,
        help="maximum ticket Attempts (default: 10)",
    )
    parsed = parser.parse_args(argv)

    try:
        config_file = _default_config_path()
        document = _read_config_file(config_file)
        opencode = _opencode_settings(document)
        records = RunRecordSettings(directory=_default_records_dir())
    except _ConfigurationError as error:
        parser.error(str(error))

    return Options(
        spec_number=parsed.spec_number,
        max_iterations=parsed.max_iterations,
        opencode=opencode,
        records=records,
        config_file=config_file if document is not None else None,
    )
