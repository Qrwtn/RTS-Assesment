"""
Shared test fixtures.

Tests run against a real PostgreSQL database (DATABASE_URL env var).
In CI this is the postgres service container defined in ci.yml.
Locally, run `make test-db-up` to spin up a throwaway Postgres via Docker,
then `make test` — or just `docker compose up db -d` and run pytest directly.

Redis calls are patched out (no-op) so you don't need Redis running for tests.
CSRF validation is disabled via FastAPI dependency_overrides so tests can POST
form data without generating tokens.
"""
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

import app.database as db_module
from app.config import settings
from app.database import Base
from app.main import app
from app.middleware.csrf import csrf_protect


# ── Swap engine to NullPool BEFORE any fixture runs ──────────────────────────
#
# NullPool = no connection reuse between operations.  Each call to
# async_session_maker() checks out a fresh asyncpg connection and releases it
# immediately when the session closes.  This eliminates the
#   asyncpg.exceptions._base.InterfaceError:
#     cannot perform operation: another operation is in progress
# error that occurs when a pooled connection is left in a bad state after a
# failed test and then handed to the next test.
#
_test_engine = create_async_engine(
    settings.async_database_url,
    poolclass=NullPool,
)
_test_session_maker = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)

# Patch the module-level singletons so every code path that does
# `from app.database import engine` or `async_session_maker` sees the test engine.
db_module.engine = _test_engine
db_module.async_session_maker = _test_session_maker


# ── Schema setup ──────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="session", autouse=True)
async def create_tables():
    """Create all tables once per session; drop them after."""
    from app.modules.user import models  # noqa: F401

    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ── Per-test DB session ───────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db_session():
    """Yield a session that rolls back after each test — keeps tests isolated."""
    async with _test_session_maker() as session:
        try:
            yield session
        finally:
            await session.rollback()


# ── HTTP client ───────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client(monkeypatch):
    """
    HTTPX async test client with:
      - Redis patched out (no-op) — cache_get/set/delete/ping/get_redis all mocked
      - CSRF dependency overridden (no-op) so tests can POST without tokens
      - Rate limiter counters reset before each test so 429s don't bleed across tests
    """
    import app.cache as cache_module

    # Patch all Redis helpers so nothing actually tries to connect to Redis.
    monkeypatch.setattr(cache_module, "cache_get",  AsyncMock(return_value=None))
    monkeypatch.setattr(cache_module, "cache_set",  AsyncMock())
    monkeypatch.setattr(cache_module, "cache_delete", AsyncMock())
    monkeypatch.setattr(cache_module, "ping",        AsyncMock(return_value=True))
    # get_redis returns a mock Redis client whose async methods are also awaitable.
    # MagicMock() alone would fail on `await r.get(key)` because attribute access on
    # MagicMock returns plain MagicMocks, which can't be awaited.
    _mock_redis = MagicMock()
    _mock_redis.get = AsyncMock(return_value=None)
    _mock_redis.setex = AsyncMock(return_value=True)
    _mock_redis.delete = AsyncMock(return_value=1)
    _mock_redis.ping = AsyncMock(return_value=True)
    monkeypatch.setattr(cache_module, "get_redis", AsyncMock(return_value=_mock_redis))

    # Also patch the name that main.py bound at import time:
    #   from app.cache import ping as redis_ping
    # Patching app.cache.ping alone doesn't affect that local binding.
    import app.main as main_module
    monkeypatch.setattr(main_module, "redis_ping", AsyncMock(return_value=True))

    # Reset slowapi in-memory rate-limit counters so tests can't 429-each-other.
    from app.limiter import limiter
    limiter.reset()

    # The lifespan doesn't run in tests, so boot_nonce is never set.
    # Stamp a fixed nonce so auth_guard's check passes without erroring.
    import secrets
    app.state.boot_nonce = secrets.token_hex(16)

    # Override CSRF dependency — tests submit plain form data, no token needed
    async def _no_csrf():
        return None

    app.dependency_overrides[csrf_protect] = _no_csrf

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    # Clean up override after each test
    app.dependency_overrides.pop(csrf_protect, None)
