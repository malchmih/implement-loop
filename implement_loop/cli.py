"""Public command entry point."""

from __future__ import annotations

import signal
import sys
from collections.abc import Sequence
from types import FrameType

from .config import parse_args
from .errors import LoopError
from .runner import run


def _handle_termination(_signum: int, _frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def main(argv: Sequence[str] | None = None) -> int:
    options = parse_args(argv)
    signal.signal(signal.SIGTERM, _handle_termination)
    try:
        run(options)
    except KeyboardInterrupt:
        print(
            "Interrupted. Checkout changes and issue assignments were preserved. "
            "Verify the selected harness, delegated agents, and background work have stopped "
            "before rerunning; releasing the local lock does not confirm shutdown.",
            file=sys.stderr,
        )
        return 130
    except LoopError as error:
        print(f"Error: {error}", file=sys.stderr)
        print(
            "Loop stopped; checkout changes, commits, and assignments were preserved. "
            "If the selected harness, delegated agents, or background work may still be running, "
            "verify they have stopped before rerunning.",
            file=sys.stderr,
        )
        return 1
    except OSError as error:
        print(f"Error: local file or process operation failed: {error}", file=sys.stderr)
        print(
            "Loop stopped; checkout changes, commits, and assignments were preserved. "
            "If the selected harness, delegated agents, or background work may still be running, "
            "verify they have stopped before rerunning.",
            file=sys.stderr,
        )
        return 1
    return 0
