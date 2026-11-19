"""Request ID, access logging and security headers."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response

from worldscope.core.logging import get_logger

log = get_logger("worldscope.access")

_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
# The API serves JSON only, so it can forbid everything a browser might load.
API_CSP = "default-src 'none'; frame-ancestors 'none'"

CallNext = Callable[[Request], Awaitable[Response]]


def request_id_for(request: Request) -> str:
    incoming = request.headers.get("x-request-id", "")
    return incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex


async def request_context(request: Request, call_next: CallNext) -> Response:
    request_id = request_id_for(request)
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - started) * 1000, 1)

    response.headers["X-Request-ID"] = request_id
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    if not request.url.path.startswith(_DOCS_PATHS):
        response.headers.setdefault("Content-Security-Policy", API_CSP)

    if request.url.path not in ("/health", "/health/ready"):
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
    return response
