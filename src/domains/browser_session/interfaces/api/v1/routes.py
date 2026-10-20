from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.base.authentication.rbac import require_roles_and_scopes
from src.base.entities.user import User
from src.domains.browser_session.dependencies.get_browser_session_service import (
    BrowserSessionServiceDep,
)
from src.domains.browser_session.interfaces.api.v1.models import (
    BrowserSessionCallbackPayload,
    BrowserSessionRenderRequest,
    BrowserSessionRenderResponse,
    BrowserSessionResponse,
    BrowserSessionStatusRequest,
    BrowserSessionStatusResponse,
    DeleteBrowserSessionResponse,
    StartBrowserSessionRequest,
)
from src.domains.browser_session.services.browser_session_service import (
    BrowserSessionServiceError,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/browser-sessions",
    tags=["browser-sessions"],
)

def _raise_service_error(
    *,
    operation: str,
    error: BrowserSessionServiceError,
) -> None:
    logger.warning(
        "Browser session %s failed: %s",
        operation,
        error,
    )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(error),
    ) from error


@router.post(
    "/start",
    response_model=BrowserSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_browser_session(
    payload: StartBrowserSessionRequest,
    browser_session_service: BrowserSessionServiceDep,
    user: User = Depends(require_roles_and_scopes()),
) -> BrowserSessionResponse:
    try:
        result = await browser_session_service.start_session(
            user_id=str(user.id),
            target_url=str(payload.target_url),
            tenant_id=payload.tenant_id,
            browser_session_id=payload.browser_session_id,
            ttl_seconds=payload.ttl_seconds,
            headed=payload.headed,
            success_url_contains=payload.success_url_contains,
        )

        return BrowserSessionResponse.model_validate(result)

    except BrowserSessionServiceError as exc:
        logger.warning(
            "Browser session start failed: %s",
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post(
    "/callback",
    response_model=BrowserSessionResponse,
)
async def browser_session_callback(
    payload: BrowserSessionCallbackPayload,
    request: Request,
    browser_session_service: BrowserSessionServiceDep,
) -> BrowserSessionResponse:
    """
    Receive browser-runner auto-capture callback.

    This endpoint is called by Browser Runner, not by the frontend.
    It validates the callback token, stores captured browser state,
    marks the browser session as authenticated, and publishes the
    browser_session.authenticated event.
    """

    callback_token = request.headers.get(
        "x-browser-runner-callback-token"
    )

    try:
        result = await browser_session_service.handle_auto_capture_callback(
            payload=payload,
            callback_token=callback_token,
        )

        return BrowserSessionResponse.model_validate(result)

    except BrowserSessionServiceError as exc:
        _raise_service_error(
            operation="callback",
            error=exc,
        )

@router.post(
    "/{browser_session_id}/render",
    response_model=BrowserSessionRenderResponse,
)
async def render_browser_session_page(
    browser_session_id: str,
    payload: BrowserSessionRenderRequest,
    service: BrowserSessionServiceDep,
):
    try:
        rendered = await service.render_page(
            user_id=payload.user_id,
            browser_session_id=browser_session_id,
            url=payload.url,
            tenant_id=payload.user_id,
            wait_until=payload.wait_until,
            wait_ms=payload.wait_ms,
            max_html_bytes=payload.max_html_bytes,
        )

        return BrowserSessionRenderResponse(
            browser_session_id=browser_session_id,
            **rendered,
        )

    except BrowserSessionServiceError as exc:
        detail = str(exc)

        if "not found" in detail.lower():
            status_code = 404
        elif "not ready" in detail.lower() or "not authenticated" in detail.lower():
            status_code = 409
        elif "outside browser-session domain" in detail.lower():
            status_code = 403
        else:
            status_code = 400

        raise HTTPException(
            status_code=status_code,
            detail=detail,
        ) from exc
    

@router.post(
    "/render",
    response_model=BrowserSessionRenderResponse,
)
async def render_ephemeral_page(
    payload: BrowserSessionRenderRequest,
    service: BrowserSessionServiceDep,
):
    try:
        rendered = await service.render_ephemeral_page(
            url=payload.url,
            tenant_id=payload.user_id,
            wait_until=payload.wait_until,
            wait_ms=payload.wait_ms,
            max_html_bytes=payload.max_html_bytes,
        )

        return BrowserSessionRenderResponse(
            **rendered,
        )

    except BrowserSessionServiceError as exc:
        detail = str(exc)

        if "not found" in detail.lower():
            status_code = 404
        elif "not ready" in detail.lower():
            status_code = 409
        elif "outside browser-session domain" in detail.lower():
            status_code = 403
        else:
            status_code = 400

        raise HTTPException(
            status_code=status_code,
            detail=detail,
        ) from exc

@router.post(
    "/status",
    response_model=BrowserSessionStatusResponse,
)
async def browser_session_status(
    payload: BrowserSessionStatusRequest,
    request: Request,
    browser_session_service: BrowserSessionServiceDep,
    user: User = Depends(require_roles_and_scopes()),
) -> BrowserSessionStatusResponse:
    """
    Return safe browser-session metadata.
    """

    try:
        result = await browser_session_service.get_status(
            user_id=str(user.id),
            browser_session_id=payload.browser_session_id,
            tenant_id=payload.tenant_id,
        )

        return BrowserSessionStatusResponse.model_validate(result)

    except BrowserSessionServiceError as exc:
        _raise_service_error(
            operation="status",
            error=exc,
        )


@router.delete(
    "/{browser_session_id}",
    response_model=DeleteBrowserSessionResponse,
)
async def delete_browser_session(
    browser_session_id: str,
    request: Request,
    browser_session_service: BrowserSessionServiceDep,
    tenant_id: str | None = None,
    user: User = Depends(require_roles_and_scopes()),
) -> DeleteBrowserSessionResponse:
    """
    Close the Browser Runner session and delete server-side session state.
    """
    
    try:
        result = await browser_session_service.delete_session(
            user_id=str(user.id),
            browser_session_id=browser_session_id,
            tenant_id=tenant_id,
        )

        return DeleteBrowserSessionResponse.model_validate(result)

    except BrowserSessionServiceError as exc:
        _raise_service_error(
            operation="delete",
            error=exc,
        )