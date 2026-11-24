"""Response envelope matching IAM: response_header (status, error code) + response_body."""
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


def ok(body: Any = None) -> dict:
    return {"response_header": {"status": "SUCCESS", "error_code": None, "error_message": None}, "response_body": body}


def fail(code: str, message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"response_header": {"status": "ERROR", "error_code": code, "error_message": message}, "response_body": None},
    )


class RbacError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code, self.message, self.status_code = code, message, status_code


async def rbac_error_handler(_: Request, exc: RbacError) -> JSONResponse:
    return fail(exc.code, exc.message, exc.status_code)
