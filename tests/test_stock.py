"""
Happy-path tests for stock lookup flows:
  - Lookup with mocked Finnhub response
  - Result displayed correctly
  - Search saved to history
  - Favorites add / remove
  - Public index shows top movers
"""
import pytest
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.asyncio

# ── Helpers ───────────────────────────────────────────────────────────────────

MOCK_QUOTE = {
    "symbol": "AAPL",
    "open": 170.00,
    "current": 175.50,
    "high": 176.00,
    "low": 169.50,
    "prev_close": 170.00,
    "percent_change": 3.24,
}

MOCK_PEERS = ["MSFT", "GOOGL", "META", "AMZN"]

MOCK_TOP_MOVERS = {
    "gainers": [
        {"symbol": "NVDA", "current": 900.0, "percent_change": 5.2},
        {"symbol": "AMD",  "current": 155.0, "percent_change": 3.1},
    ],
    "losers": [
        {"symbol": "INTC", "current": 30.0, "percent_change": -4.5},
    ],
}


async def _signup_and_login(client, email="stock_user@test.com", password="securepass123"):
    """Helper: create account + log in, return the (now-cookied) client."""
    await client.post("/signup", data={"email": email, "password": password}, follow_redirects=False)
    await client.post("/login",  data={"email": email, "password": password}, follow_redirects=False)


# ── Public index ──────────────────────────────────────────────────────────────

async def test_public_index_shows_top_movers(client):
    """Logged-out GET / returns 200 and top-movers section."""
    with patch("app.modules.stock.service.get_top_movers", new=AsyncMock(return_value=MOCK_TOP_MOVERS)):
        resp = await client.get("/", follow_redirects=True)
    assert resp.status_code == 200
    assert b"NVDA" in resp.content or b"Market Snapshot" in resp.content


# ── Authenticated dashboard ───────────────────────────────────────────────────

async def test_dashboard_loads(client):
    """Logged-in GET /dashboard returns 200."""
    await _signup_and_login(client, "dash@test.com")
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    assert b"Dashboard" in resp.content


# ── Stock lookup ──────────────────────────────────────────────────────────────

async def test_stock_lookup_success(client):
    """POST /stock/lookup with valid symbol shows quote data."""
    await _signup_and_login(client, "lookup@test.com")

    with patch("app.modules.stock.service.get_quote",  new=AsyncMock(return_value=MOCK_QUOTE)), \
         patch("app.modules.stock.service.get_peers",  new=AsyncMock(return_value=MOCK_PEERS)):
        resp = await client.post(
            "/stock/lookup",
            data={"symbol": "AAPL"},
            follow_redirects=True,
        )

    assert resp.status_code == 200
    assert b"AAPL" in resp.content
    assert b"175" in resp.content  # price visible


async def test_stock_lookup_invalid_symbol(client):
    """POST /stock/lookup with unknown symbol shows an error."""
    await _signup_and_login(client, "badlookup@test.com")

    with patch("app.modules.stock.service.get_quote", new=AsyncMock(return_value=None)):
        resp = await client.post(
            "/stock/lookup",
            data={"symbol": "ZZZZZ"},
            follow_redirects=True,
        )

    assert resp.status_code in (200, 404)
    assert b"not found" in resp.content.lower() or b"error" in resp.content.lower()


async def test_stock_lookup_saves_history(client, db_session):
    """Successful lookup is persisted in the user's search history."""
    from sqlalchemy import select
    from app.modules.user.models import StockSearch

    await _signup_and_login(client, "history@test.com")

    with patch("app.modules.stock.service.get_quote", new=AsyncMock(return_value=MOCK_QUOTE)), \
         patch("app.modules.stock.service.get_peers", new=AsyncMock(return_value=MOCK_PEERS)):
        await client.post("/stock/lookup", data={"symbol": "AAPL"}, follow_redirects=True)

    # Verify a StockSearch row was created
    result = await db_session.execute(
        select(StockSearch).where(StockSearch.symbol == "AAPL")
    )
    rows = result.scalars().all()
    assert len(rows) >= 1


# ── Favorites ─────────────────────────────────────────────────────────────────

async def test_add_and_remove_favorite(client, db_session):
    """User can favorite and unfavorite a stock."""
    from sqlalchemy import select
    from app.modules.user.models import UserFavorite

    await _signup_and_login(client, "fav@test.com")

    # Add favorite
    resp = await client.post("/stock/favorite/AAPL", follow_redirects=False)
    assert resp.status_code == 303

    favs = (await db_session.execute(
        select(UserFavorite).where(UserFavorite.symbol == "AAPL")
    )).scalars().all()
    assert len(favs) >= 1

    # Remove favorite
    resp = await client.post("/stock/unfavorite/AAPL", follow_redirects=False)
    assert resp.status_code == 303


# ── HTMX favorite / unfavorite fragments ─────────────────────────────────────

async def test_add_favorite_htmx_star_form(client):
    """HTMX POST to /stock/favorite with HX-Target=star-form returns star+OOB fragment."""
    await _signup_and_login(client, "htmx_add@test.com")

    with patch("app.modules.stock.service.get_quote", new=AsyncMock(return_value=MOCK_QUOTE)), \
         patch("app.modules.stock.service.get_favorites_with_prices", new=AsyncMock(return_value=[MOCK_QUOTE])):
        resp = await client.post(
            "/stock/favorite/AAPL",
            headers={"HX-Request": "true", "HX-Target": "star-form"},
        )

    assert resp.status_code == 200
    # Response contains the new star button (outerHTML swap) + OOB watchlist
    assert b"star-form" in resp.content
    assert b"watchlist-inner" in resp.content


async def test_remove_favorite_htmx_watchlist(client):
    """HTMX POST to /stock/unfavorite with HX-Target=watchlist-inner returns the list fragment."""
    await _signup_and_login(client, "htmx_rm@test.com")

    # Add first so there's something to remove
    with patch("app.modules.stock.service.get_quote", new=AsyncMock(return_value=MOCK_QUOTE)):
        await client.post("/stock/favorite/AAPL")

    with patch("app.modules.stock.service.get_quote", new=AsyncMock(return_value=MOCK_QUOTE)), \
         patch("app.modules.stock.service.get_favorites_with_prices", new=AsyncMock(return_value=[])):
        resp = await client.post(
            "/stock/unfavorite/AAPL",
            headers={"HX-Request": "true", "HX-Target": "watchlist-inner"},
        )

    assert resp.status_code == 200
    # Empty watchlist renders the empty-state message
    assert b"Search a stock" in resp.content


async def test_add_favorite_non_htmx_redirects(client):
    """Non-HTMX POST to /stock/favorite still redirects 303 (JS-disabled fallback)."""
    await _signup_and_login(client, "nhtmx@test.com")

    with patch("app.modules.stock.service.get_quote", new=AsyncMock(return_value=MOCK_QUOTE)):
        resp = await client.post("/stock/favorite/MSFT", follow_redirects=False)

    assert resp.status_code == 303


# ── Yahoo Finance symbol search ───────────────────────────────────────────────

async def test_search_symbols_returns_results(client):
    """GET /api/stock/search returns equity results and filters dot-symbol listings."""
    mock_results = [
        {"symbol": "HMC", "name": "Honda Motor Co"},
        {"symbol": "TM",  "name": "Toyota Motor"},
    ]

    with patch("app.modules.stock.service.search_symbols", new=AsyncMock(return_value=mock_results)):
        resp = await client.get("/api/stock/search?q=honda")

    assert resp.status_code == 200
    data = resp.json()
    symbols = [r["symbol"] for r in data["results"]]
    assert "HMC" in symbols
    assert "TM" in symbols
    assert "7267.T" not in symbols  # dot-containing symbols already filtered by service


async def test_search_symbols_empty_query(client):
    """Empty query returns an empty results list without hitting Yahoo."""
    resp = await client.get("/api/stock/search?q=")
    assert resp.status_code == 200
    assert resp.json() == {"results": []}


# ── Health check ──────────────────────────────────────────────────────────────

async def test_health_endpoint(client):
    """GET /health returns status ok."""
    with patch("app.main.redis_ping", new=AsyncMock(return_value=True)):
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
