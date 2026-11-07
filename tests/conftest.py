from __future__ import annotations

from collections.abc import Iterator

import pytest
from support import LoopFixture


@pytest.fixture
def loop_fixture() -> Iterator[LoopFixture]:
    """Provide a temporary Git checkout with fake GitHub and OpenCode commands."""
    fixture = LoopFixture()
    try:
        yield fixture
    finally:
        fixture.cleanup()
