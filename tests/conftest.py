from __future__ import annotations

from collections.abc import Iterator

import pytest
from support import LoopTestCase


@pytest.fixture
def cli_case() -> Iterator[LoopTestCase]:
    """Provide a temporary Git checkout with fake GitHub and OpenCode commands."""
    case = LoopTestCase()
    try:
        case.setUp()
        yield case
    finally:
        case.doCleanups()
