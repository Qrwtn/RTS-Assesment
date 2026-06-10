"""
CSRF protection — double-submit session token pattern.

How it works:
  1. CSRFTokenMiddleware runs on every request (after SessionMiddleware).
     It generates a random token the first time and stores it in the session,
     then sets request.state.csrf_token so templates can access it via
     {{ request.state.csrf_token }}.
  2. csrf_protect is a FastAPI dependency injected into every mutating
     route (POST/PUT/DELETE on form-based endpoints). It reads the
     `csrf_token` field from the submitted form and compares it against
     the session token using a constant-time comparison.

API routes (/api/*) are excluded from validation because they are called
by our own JS with fetch() and receive no session cookie from third-party
pages.
"""

import secrets

from fastapi import Form, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware


class CSRFTokenMiddleware(BaseHTTPMiddleware):
    """Generate and expose the CSRF token — no validation here."""

    async def dispatch(self, request: Request, call_next):
        # Ensure a token exists for this session
        if "csrf_token" not in request.session:
            request.session["csrf_token"] = secrets.token_hex(32)
        # Expose on request.state so templates reach it as request.state.csrf_token
        request.state.csrf_token = request.session["csrf_token"]
        return await call_next(request)


async def csrf_protect(
    request: Request,
    csrf_token: str = Form(default=""),
) -> None:
    """
    FastAPI dependency: reject the request if the submitted csrf_token
    doesn't match the one stored in the session.

    Usage:
        @router.post("/some/route")
        async def handler(..., _csrf=Depends(csrf_protect)):
            ...
    """
    expected = request.session.get("csrf_token", "")
    if not expected or not secrets.compare_digest(csrf_token, expected):
        raise HTTPException(
            status_code=403,
            detail="CSRF validation failed. Please refresh the page and try again.",
        )
