from typing import Any

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


def error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    content: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": request.state.request_id,
    }
    if details:
        content["details"] = details
    return JSONResponse(status_code=status_code, content=content)


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        details = [
            {
                "location": ".".join(str(item) for item in issue["loc"]),
                "message": issue["msg"],
                "code": issue["type"],
            }
            for issue in error.errors()
        ]
        return error_response(
            request,
            status_code=422,
            code="validation_error",
            message="Request validation failed",
            details=details,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        code = "http_error"
        message = str(error.detail)
        if isinstance(error.detail, dict):
            code = str(error.detail.get("code", code))
            message = str(error.detail.get("message", message))
        response = error_response(
            request,
            status_code=error.status_code,
            code=code,
            message=message,
        )
        if error.headers:
            response.headers.update(error.headers)
        return response

    @app.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        logger.exception(
            "Unhandled API error",
            extra={"request_id": request.state.request_id},
        )
        return error_response(
            request,
            status_code=500,
            code="internal_error",
            message="An unexpected error occurred",
        )
