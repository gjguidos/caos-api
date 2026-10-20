import asyncio

import pytest

from src.base.operations.models import (
    OperationProgress,
    OperationRecord,
)
from src.base.operations.repository import (
    InMemoryOperationRepository,
)


@pytest.mark.asyncio
async def test_progress_revision_is_atomic() -> None:
    repository = InMemoryOperationRepository()

    operation = OperationRecord(
        run_id="test-run",
        kind="crawl",
        producer="web_crawler",
    )

    await repository.create(operation)

    async def update(value: int) -> None:
        await repository.update_progress(
            operation.run_id,
            progress=OperationProgress(
                phase="page_acquisition",
                current=value,
                total=1000,
            ),
        )

    await asyncio.gather(
        *(update(value) for value in range(1, 101))
    )

    updated = await repository.get(operation.run_id)

    assert updated is not None
    assert updated.progress_revision == 100