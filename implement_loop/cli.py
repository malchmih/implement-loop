"""Public command entry point."""

from __future__ import annotations

import signal
import sys
from collections.abc import Sequence
from types import FrameType

from .config import parse_args
from .errors import DiagnosticContext, LoopError
from .runner import run


def _handle_termination(_signum: int, _frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def _print_diagnostic_context(context: DiagnosticContext) -> None:
    if context.run_records is not None and context.run_records.is_dir():
        print(f"Run records: {context.run_records}", file=sys.stderr)

    if context.ticket_number is None:
        return
    if context.ticket_records is not None and context.ticket_records.is_dir():
        invocation_note = "" if context.harness_invoked else " (no harness invocation started)"
        print(
            f"Ticket #{context.ticket_number} records{invocation_note}: {context.ticket_records}",
            file=sys.stderr,
        )
    else:
        print(
            f"Ticket #{context.ticket_number} selected; no existing ticket-record directory "
            "is available.",
            file=sys.stderr,
        )


def _print_recovery_guidance(*, interrupted: bool) -> None:
    message = (
        "Any existing checkout changes, commits, and assignments were left in place. "
        "Before rerunning, verify that any potentially running harness, delegated agents, "
        "and background work have stopped."
    )
    if interrupted:
        message += (
            " This output, the records, and release of the local lock do not confirm shutdown."
        )
    print(message, file=sys.stderr)


def _report_failure(message: str, diagnostics: DiagnosticContext) -> None:
    print(message, file=sys.stderr)
    _print_diagnostic_context(diagnostics)
    _print_recovery_guidance(interrupted=False)


def main(argv: Sequence[str] | None = None) -> int:
    options = parse_args(argv)
    signal.signal(signal.SIGTERM, _handle_termination)
    diagnostics = DiagnosticContext()
    try:
        run(options, diagnostics)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        _print_diagnostic_context(diagnostics)
        _print_recovery_guidance(interrupted=True)
        return 130
    except LoopError as error:
        _report_failure(f"Error: {error}", diagnostics)
        return 1
    except OSError as error:
        _report_failure(f"Error: local file or process operation failed: {error}", diagnostics)
        return 1
    return 0
