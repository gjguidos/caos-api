from __future__ import annotations

from dependency_injector.wiring import (
    Provide,
    inject,
)
from fastapi import Depends

from src.base.dependencies.di_container import (
    Container,
)
from src.base.operations.runner import (
    OperationRunner,
)


@inject
def get_operation_runner(
    operation_runner: OperationRunner = Depends(
        Provide[
            Container.operations.operation_runner
        ]
    ),
) -> OperationRunner:
    return operation_runner