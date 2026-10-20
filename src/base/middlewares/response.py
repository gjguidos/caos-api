from __future__ import annotations

import json
import logging
import platform
from typing import Any

from starlette.exceptions import (
    HTTPException as StarletteHTTPException,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from src.base.services.consumer_info import (
    ConsumerInfoService,
)

logger = logging.getLogger("response middleware")


class ResponseFormatMiddleware(
    BaseHTTPMiddleware,
):
    """
    Standardize normal API responses.

    Streaming/download responses are returned untouched.
    """

    def __init__(
        self,
        app: ASGIApp,
    ) -> None:
        super().__init__(app)

        self.consumer_info_service = (
            ConsumerInfoService()
        )

        self.skip_paths = [
            "/docs",
            "/redoc",
            "/openapi.json",
            "/swagger-ui/index.html",
            "/v3/api-docs",
            "/api/v1/dev/files/",
        ]

    def _should_skip_formatting(
        self,
        request: Request,
    ) -> bool:
        """
        Determine whether this request must bypass
        response formatting entirely.
        """
        path = request.url.path

        if any(
            path.startswith(skip_path)
            for skip_path in self.skip_paths
        ):
            return True

        # Crawler reports can be large streaming downloads.
        # They must never be consumed/rebuilt by this middleware.
        if (
            path.startswith(
                "/api/v1/operations/"
            )
            and "/reports/" in path
        ):
            return True

        return False

    def _should_skip_response_formatting(
        self,
        response: Response,
    ) -> bool:
        """
        Determine whether an already-created response
        should be returned untouched.
        """
        if (
            response.status_code < 200
            or response.status_code
            in (204, 205, 304)
        ):
            return True

        content_disposition = (
            response.headers.get(
                "content-disposition",
                "",
            )
        )

        if (
            content_disposition
            .lower()
            .startswith("attachment")
        ):
            return True

        return False

    def _is_localhost(
        self,
        request: Request,
    ) -> bool:
        host = request.headers.get(
            "host",
            "",
        )

        return (
            host.startswith("localhost:")
            or host.startswith("127.0.0.1:")
        )

    def _extract_message_from_response(
        self,
        body_data: Any,
    ) -> str:
        if isinstance(
            body_data,
            str,
        ):
            return body_data

        if isinstance(
            body_data,
            dict,
        ):
            message = body_data.get(
                "message"
            )

            if isinstance(
                message,
                str,
            ):
                return message

            error = body_data.get(
                "error"
            )

            if isinstance(
                error,
                dict,
            ):
                detail = error.get(
                    "detail"
                )

                if isinstance(
                    detail,
                    str,
                ):
                    return detail

            detail = body_data.get(
                "detail"
            )

            if isinstance(
                detail,
                str,
            ):
                return detail

        return "An error occurred"

    async def dispatch(
        self,
        request: Request,
        call_next,
    ):
        # Important:
        # Known streaming/download endpoints bypass all
        # formatter body inspection.
        if self._should_skip_formatting(
            request
        ):
            return await call_next(
                request
            )

        try:
            response = await call_next(
                request
            )

            # Generic safeguard for downloads and
            # body-less responses.
            if self._should_skip_response_formatting(
                response
            ):
                return response

        except StarletteHTTPException as exc:
            standardized_response = {
                "success": False,
                "statusCode": exc.status_code,
                "message": exc.detail,
            }

            if self._is_localhost(
                request
            ):
                standardized_response[
                    "pythonVersion"
                ] = platform.python_version()

                standardized_response[
                    "systemVersion"
                ] = platform.platform()

            return JSONResponse(
                content=standardized_response,
                status_code=exc.status_code,
            )

        except Exception as exc:
            logger.error(
                (
                    "Unhandled exception in "
                    "ResponseFormatMiddleware during "
                    f"{request.method} "
                    f"{request.url.path}: "
                    f"{exc!r}"
                ),
                exc_info=True,
            )

            standardized_response = {
                "success": False,
                "statusCode": 500,
                "message": (
                    "An unexpected error occurred."
                ),
            }

            if self._is_localhost(
                request
            ):
                standardized_response[
                    "pythonVersion"
                ] = platform.python_version()

                standardized_response[
                    "systemVersion"
                ] = platform.platform()

                standardized_response[
                    "error"
                ] = str(exc)

            return JSONResponse(
                content=standardized_response,
                status_code=500,
            )

        # Normal API responses can still be consumed
        # and standardized.
        if hasattr(
            response,
            "body_iterator",
        ):
            body = b"".join(
                [
                    chunk
                    async for chunk
                    in response.body_iterator
                ]
            )

            try:
                body_data = json.loads(
                    body.decode(
                        "utf-8"
                    )
                )
            except (
                json.JSONDecodeError,
                UnicodeDecodeError,
            ):
                body_data = None

        else:
            body = getattr(
                response,
                "body",
                None,
            )

            body_data = body

            if body:
                try:
                    if isinstance(
                        body,
                        bytes,
                    ):
                        body_data = (
                            json.loads(
                                body.decode(
                                    "utf-8"
                                )
                            )
                        )
                    elif isinstance(
                        body,
                        str,
                    ):
                        body_data = (
                            json.loads(
                                body
                            )
                        )

                except (
                    json.JSONDecodeError,
                    UnicodeDecodeError,
                ):
                    pass

        standardized_response: dict[
            str,
            Any,
        ] = {
            "success": (
                response.status_code
                < 400
            ),
            "statusCode": (
                response.status_code
            ),
        }

        if response.status_code < 400:
            if body_data is not None:
                standardized_response[
                    "data"
                ] = body_data

            standardized_response[
                "message"
            ] = (
                "Operation completed "
                "successfully"
            )

        else:
            if body_data is not None:
                standardized_response[
                    "message"
                ] = (
                    self
                    ._extract_message_from_response(
                        body_data
                    )
                )

                if (
                    isinstance(
                        body_data,
                        dict,
                    )
                    and "data"
                    in body_data
                    and body_data[
                        "data"
                    ]
                    is not None
                ):
                    standardized_response[
                        "data"
                    ] = body_data[
                        "data"
                    ]

            else:
                standardized_response[
                    "message"
                ] = "An error occurred"

        if self._is_localhost(
            request
        ):
            standardized_response[
                "pythonVersion"
            ] = platform.python_version()

            standardized_response[
                "systemVersion"
            ] = platform.platform()

        return JSONResponse(
            content=standardized_response,
            status_code=response.status_code,
        )