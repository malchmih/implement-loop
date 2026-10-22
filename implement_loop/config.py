"""Command-line configuration for the ticket runner."""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from dataclasses import dataclass

_POSITIVE_INTEGER = re.compile(r"^[1-9][0-9]{0,8}$")


@dataclass(frozen=True)
class Options:
    """Options accepted by the public executable."""

    spec_number: int
    max_iterations: int
    harness: str


def _positive_integer(value: str) -> int:
    if not _POSITIVE_INTEGER.fullmatch(value):
        raise argparse.ArgumentTypeError("must be a positive integer with at most 9 digits")
    return int(value)


def parse_args(argv: Sequence[str] | None = None) -> Options:
    """Parse the compact spec-number invocation and the existing limit option."""

    parser = argparse.ArgumentParser(
        prog="implement-loop",
        description=("Implement eligible, prepared direct child issues of a GitHub spec."),
        epilog=(
            "Each selected-harness invocation is one attempt. The loop stops successfully "
            "when no eligible child remains."
        ),
    )
    parser.add_argument("spec_number", metavar="SPEC_NUMBER", type=_positive_integer)
    parser.add_argument(
        "--harness",
        choices=("opencode", "copilot"),
        default="opencode",
        help="agent CLI to run (default: opencode)",
    )
    parser.add_argument(
        "--max-iterations",
        metavar="N",
        type=_positive_integer,
        default=5,
        help="maximum selected-harness invocations (default: 5)",
    )
    parsed = parser.parse_args(argv)
    return Options(
        spec_number=parsed.spec_number,
        max_iterations=parsed.max_iterations,
        harness=parsed.harness,
    )
