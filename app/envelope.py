"""Response envelope matching IAM: response_header (status, error code) + response_body."""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from openg2p_fastapi_common.errors import BaseAppException
from openg2p_fastapi_common.exception import BaseExceptionHandler


def ok(body: Any = None) -> dict:
    return {
        "response_header": {
            "status": "SUCCESS",
            "error_code": None,
            "error_message": None,
        },
        "response_body": body,
    }


def fail(code: str, message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "response_header": {
                "status": "ERROR",
                "error_code": code,
                "error_message": message,
            },
            "response_body": None,
        },
    )


class RbacError(BaseAppException):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(code, message, http_status_code=status_code)


class RbacExceptionHandler(BaseExceptionHandler):
    """Same library handlers, but errors use the envelope above instead of `{"errors": [...]}`."""

    async def base_exception_handler(self, request: Request, exc: BaseAppException):
        if exc.status_code >= 500:
            await super().base_exception_handler(request, exc)  # logs the traceback
        return fail(exc.code, exc.message, exc.status_code)

    async def request_validation_exception_handler(self, request: Request, exc):
        message = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        return fail("INVALID_INPUT", message, 422)

    async def unknown_exception_handler(self, request: Request, exc: Exception):
        await super().unknown_exception_handler(request, exc)  # logs the traceback
        return fail("INTERNAL_ERROR", "Unknown Error.", 500)
