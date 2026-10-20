"""Read API for persisted agent run reports.

Ownership mirrors how agents already work: a caller sees the runs they started,
and ``agentverse-admin`` sees everything. The filter is applied in the query
rather than after the fetch so a non-owner never learns a run exists.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from src.base.authentication.rbac import require_roles_and_scopes
from src.base.authentication.role import Role
from src.base.entities.user import User
from src.domains.agentverse.dependencies.get_run_report_repository import (
    get_run_report_repository,
)
from src.domains.agentverse.entities.run_report import AgentRunReport
from src.domains.agentverse.repositories.run_report_repository import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    RunReportRepository,
)

logger = logging.getLogger("agentverse.run_report_routes")
router = APIRouter(tags=["Reports"])

_ANY_AGENTVERSE_USER = [[Role.USER], [Role.ADMIN]]


def _is_superadmin(user: User) -> bool:
    return Role.ADMIN.value in (user.roles or [])


@router.get("/api/v1/runs/{run_id}", response_model=AgentRunReport)
async def get_run_report(
    run_id: str,
    repository: RunReportRepository = Depends(get_run_report_repository),
    user: User = Depends(require_roles_and_scopes(required_roles=_ANY_AGENTVERSE_USER)),
) -> AgentRunReport:
    """Retrieve the run report for a single orchestrated turn."""
    report = await repository.get(
        run_id,
        created_by=None if _is_superadmin(user) else user.id,
    )

    # A run owned by someone else is reported as missing, not as forbidden.
    if not report:
        raise HTTPException(status_code=404, detail=f"Run report '{run_id}' not found")

    return report


@router.get("/api/v1/runs", response_model=list[AgentRunReport])
async def list_run_reports(
    session_id: str,
    limit: int = Query(DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    skip: int = Query(0, ge=0),
    repository: RunReportRepository = Depends(get_run_report_repository),
    user: User = Depends(require_roles_and_scopes(required_roles=_ANY_AGENTVERSE_USER)),
) -> list[AgentRunReport]:
    """List the run reports for one chat session, newest first."""
    return await repository.list_by_session(
        session_id,
        created_by=None if _is_superadmin(user) else user.id,
        limit=limit,
        skip=skip,
    )
