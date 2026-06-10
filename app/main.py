import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.cache import ping as redis_ping
from app.config import settings
from app.database import init_db, async_session_maker
from app.limiter import limiter
from app.middleware.auth_guard import NotAuthenticatedException
from app.middleware.csrf import CSRFTokenMiddleware
from app.modules.auth.router import router as auth_router
from app.modules.reddit.router import router as reddit_router
from app.modules.settings.router import router as settings_router
from app.modules.stock.router import router as stock_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Generate a fresh nonce every time the process starts.
    # Sessions that pre-date this boot carry a different nonce and are rejected,
    # forcing re-login — this prevents stale sessions from surviving restarts/deploys.
    app.state.boot_nonce = secrets.token_hex(16)
    yield


app = FastAPI(title="StockApp", lifespan=lifespan)
app.state.limiter = limiter
app.mount("/static", StaticFiles(directory="app/static"), name="static")


_CSP = (
    "default-src 'self'; "
    # Inline scripts required by Tailwind CDN config block and dashboard JS
    "script-src 'self' 'unsafe-inline' "
    "cdn.tailwindcss.com cdn.jsdelivr.net cdnjs.cloudflare.com unpkg.com; "
    "style-src 'self' 'unsafe-inline'; "
    # data: for any base64 images; https: for external stock logos if added later
    "img-src 'self' data: https:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Attach baseline security headers to every response."""
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    response.headers["Content-Security-Policy"] = _CSP
    return response


# Middleware order matters: last added = outermost = runs first.
# We need:  SessionMiddleware → CSRFTokenMiddleware → app
# So add CSRF first (inner), then Session (outer).
app.add_middleware(CSRFTokenMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    https_only=settings.ENVIRONMENT == "production",
    same_site="lax",       # explicit: lax blocks CSRF from cross-site navigations
    max_age=86400,         # 24-hour session lifetime
)

app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(NotAuthenticatedException)
async def not_authenticated_handler(request: Request, exc: NotAuthenticatedException):
    return RedirectResponse(url="/login", status_code=303)


app.include_router(auth_router)
app.include_router(stock_router)
app.include_router(reddit_router)
app.include_router(settings_router)


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    """
    Liveness + readiness probe.
    Pings both Postgres and Redis; returns 503 if either is down.
    Used by Railway health checks and load balancers.
    """
    redis_ok = await redis_ping()

    db_ok = False
    try:
        async with async_session_maker() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    all_ok = redis_ok and db_ok
    payload = {
        "status": "ok" if all_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "cache": "ok" if redis_ok else "degraded",
    }
    return JSONResponse(payload, status_code=200 if all_ok else 503)
