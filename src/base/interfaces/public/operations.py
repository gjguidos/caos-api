"""
REST lifecycle actions for generic detached operations.

Auto-discovered by the base routing system through the top-level
``router`` object.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from pydantic import BaseModel

from src.base.authentication.rbac import (
    require_roles_and_scopes,
)
from src.base.authentication.role import Role
from src.base.dependencies.containers.getters.get_operation_management_service import (
    get_operation_management_service,
)
from src.base.entities.user import User
from src.base.operations.management_service import (
    OperationForbiddenError,
    OperationManagementService,
)
from src.base.operations.runner import (
    OperationAlreadyFinishedError,
    OperationNotFoundError,
    OperationNotRunningError,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Operations"],
)


class OperationCancelResponse(BaseModel):
    run_id: str
    status: Literal["cancelling"]


@router.post(
    "/{run_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_operation(
    run_id: str,
    operation_manager: OperationManagementService = Depends(
        get_operation_management_service,
    ),
    user: User = Depends(
        require_roles_and_scopes(
            required_roles=[
                [Role.USER],
            ],
        ),
    ),
) -> OperationCancelResponse:
    user_id = getattr(user, "id", None)

    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authenticated user has no identifier.",
        )

    logger.info(
        "Operation cancellation requested: "
        "run_id=%s user_id=%s",
        run_id,
        user_id,
    )

    try:
        await operation_manager.cancel(
            run_id=run_id,
            user_id=str(user_id),
        )

    except OperationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except OperationForbiddenError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    except OperationAlreadyFinishedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except OperationNotRunningError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    logger.info(
        "Operation cancellation accepted: "
        "run_id=%s user_id=%s",
        run_id,
        user_id,
    )

    return OperationCancelResponse(
        run_id=run_id,
        status="cancelling",
    )