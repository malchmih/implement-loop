"""Errors that can be reported directly to the command-line user."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class DiagnosticContext:
    """Repository and record context available when reporting a handled stop."""

    repository_name: str | None = None
    run_records: Path | None = None
    ticket_number: int | None = None
    ticket_records: Path | None = None
    harness_invoked: bool = False
    handled_failure: bool = False

    def clear_ticket(self) -> None:
        """Stop associating later run-level work with the completed ticket."""

        self.ticket_number = None
        self.ticket_records = None
        self.harness_invoked = False


class LoopError(RuntimeError):
    """A run cannot safely continue."""
