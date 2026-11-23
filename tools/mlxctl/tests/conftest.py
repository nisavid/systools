from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack, ExitStack

import pytest
import pytest_asyncio


@pytest.fixture
def cleanup() -> Iterator[ExitStack]:
    with ExitStack() as stack:
        yield stack


@pytest_asyncio.fixture(loop_scope="function")
async def async_cleanup() -> AsyncIterator[AsyncExitStack]:
    async with AsyncExitStack() as stack:
        yield stack
