"""Uniform API error format: {"error": {"code", "message", "details"}} (design DS-09)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def not_found(what: str = "Resource") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def conflict(message: str, code: str = "conflict", details: Any = None) -> ApiError:
    return ApiError(409, code, message, details)


def bad_request(message: str, code: str = "bad_request", details: Any = None) -> ApiError:
    return ApiError(400, code, message, details)


def forbidden(message: str = "You do not have permission to perform this action") -> ApiError:
    return ApiError(403, "forbidden", message)


def unauthorized(message: str = "Authentication required") -> ApiError:
    return ApiError(401, "unauthorized", message)


def _body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status_code)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
                413: "payload_too_large", 429: "rate_limited"}.get(exc.status_code, "http_error")
        return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(err.get("loc", [])), "msg": err.get("msg"), "type": err.get("type")}
            for err in exc.errors()
        ]
        return JSONResponse(_body("validation_error", "Request validation failed", details), status_code=422)
