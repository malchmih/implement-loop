"""Persist an explicit completion claim for one active Attempt."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import Any

_CONTEXT_ENVIRONMENT_VARIABLE = "IMPLEMENT_LOOP_REPORT_CONTEXT"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


def _read_context(path: Path) -> tuple[int, str, Path]:
    try:
        details = path.lstat()
        if not stat.S_ISREG(details.st_mode) or stat.S_IMODE(details.st_mode) != 0o600:
            raise ValueError("the private reporting context has an unexpected file type or mode")
        context = _read_json(path)
    except (OSError, ValueError) as error:
        raise ValueError(f"cannot read private reporting context {path}: {error}") from error

    if not isinstance(context, dict) or set(context) != {"issue", "attempt_id", "result_path"}:
        raise ValueError("the private reporting context is malformed or ambiguous")
    issue = context.get("issue")
    attempt_id = context.get("attempt_id")
    raw_result_path = context.get("result_path")
    if (
        type(issue) is not int
        or issue < 1
        or not isinstance(attempt_id, str)
        or not attempt_id
        or not isinstance(raw_result_path, str)
    ):
        raise ValueError("the private reporting context is missing required Attempt identity")

    result_path = Path(raw_result_path)
    if not result_path.is_absolute() or result_path.name != "completion-result.json":
        raise ValueError("the private reporting context has an invalid report destination")
    return issue, attempt_id, result_path


def _report(issue: int, attempt_id: str, acceptance: str, validation: str) -> dict[str, Any]:
    return {
        "issue": issue,
        "attempt_id": attempt_id,
        "status": "completed",
        "acceptance_criteria": acceptance,
        "validation": validation,
    }


def _existing_report(path: Path) -> dict[str, Any] | None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise OSError(f"cannot read existing completion report {path}: {error}") from error
    if not stat.S_ISREG(details.st_mode):
        raise ValueError("existing completion report is not a regular file")

    try:
        existing = _read_json(path)
    except OSError as error:
        raise OSError(f"cannot read existing completion report {path}: {error}") from error
    if not isinstance(existing, dict):
        raise ValueError("existing completion report is malformed or ambiguous")
    return existing


def _write_report(path: Path, report: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(
                (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
            )
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except OSError as error:
        raise OSError(f"cannot persist completion report {path}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _submit(path: Path, report: dict[str, Any]) -> tuple[bool, bool, bool]:
    existing = _existing_report(path)
    passed = report["acceptance_criteria"] == report["validation"] == "passed"
    if existing == report:
        return True, passed, False
    if existing is None:
        _write_report(path, report)
        return False, passed, False
    if existing.get("status") == "conflict":
        raise ValueError("a conflicting completion report is already persisted")

    conflict = {
        "status": "conflict",
        "issue": report["issue"],
        "attempt_id": report["attempt_id"],
        "reports": [existing, report],
    }
    _write_report(path, conflict)
    return False, False, True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="implement-loop-report",
        description="Submit explicit completion claims for the active Implement Loop Attempt.",
        epilog=(
            "Completion usage: implement-loop-report completed "
            "--acceptance-criteria passed --validation passed"
        ),
    )
    subcommands = parser.add_subparsers(dest="outcome", required=True)
    completed = subcommands.add_parser(
        "completed",
        help="report completed work after required review, validation, delegated work, and commit",
        description=(
            "Submit completion claims only after implementation, required review and validation, "
            "joined delegated work, and committing have finished."
        ),
    )
    completed.add_argument("--acceptance-criteria", required=True, choices=("passed", "failed"))
    completed.add_argument("--validation", required=True, choices=("passed", "failed"))
    return parser


def main(argv: list[str] | None = None) -> int:
    """Command entry point used only by the runner-provided private command."""

    args = _parser().parse_args(argv)
    context_value = os.environ.get(_CONTEXT_ENVIRONMENT_VARIABLE)
    if not context_value:
        print("Error: no active Attempt reporting context was supplied.", file=sys.stderr)
        return 1

    try:
        issue, attempt_id, result_path = _read_context(Path(context_value))
        report = _report(issue, attempt_id, args.acceptance_criteria, args.validation)
        repeated, passed, conflict = _submit(result_path, report)
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if not passed:
        if repeated:
            print(
                "Error: the repeated completion report does not claim that both acceptance "
                "criteria and required validation passed.",
                file=sys.stderr,
            )
        elif conflict:
            print(
                "Error: conflicting completion reports were persisted; the Attempt cannot "
                "complete automatically.",
                file=sys.stderr,
            )
        else:
            print(
                "Error: completion was recorded, but both acceptance criteria and required "
                "validation must be reported as passed.",
                file=sys.stderr,
            )
        return 1

    qualifier = "Repeated " if repeated else ""
    print(f"{qualifier}completion report saved for ticket #{issue}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
