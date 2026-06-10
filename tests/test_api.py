"""
JSON API endpoint tests.

Covers:
  - GET /api/stock/history/{symbol}   — candle data
  - GET /api/stock/search             — symbol autocomplete
  - GET /api/market/ticker            — live ticker bar
  - GET /api/portfolio                — paper portfolio (authenticated)
  - GET /api/reddit/summary           — reddit sentiment (mocked)
"""
import pytest
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.asyncio

MOCK_CANDLE = {
    "labels": ["Jun 01", "Jun 02", "Jun 03"],
    "prices": [170.0, 172.5, 175.0],
    "period": "1M",
}

MOCK_SEARCH_RESULTS = [
    {"symbol": "AAPL", "name": "Apple Inc"},
    {"symbol": "AAPL.SW", "name": "Apple Inc Swiss"},
]

MOCK_TICKER = [
    {"symbol": "SPY",  "price": 530.0, "change": 0.5},
    {"symbol": "NVDA", "price": 900.0, "change": 3.2},
]

MOCK_REDDIT = {
    "summary": "Retail investors are focused on NVDA and TSLA.",
    "tickers": [
        {"ticker": "NVDA", "name": "Nvidia", "mentions": 500, "mentions_24h_ago": 300},
        {"ticker": "TSLA", "name": "Tesla",  "mentions": 400, "mentions_24h_ago": 250},
    ],
    "timeframe": "day",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _signup_and_login(client, email, password="securepass123"):
    await client.post("/signup", data={"email": email, "password": password}, follow_redirects=False)
    await client.post("/login",  data={"email": email, "password": password}, follow_redirects=False)


# ── /api/stock/history ────────────────────────────────────────────────────────

async def test_stock_history_returns_candle_data(client):
    """GET /api/stock/history/AAPL returns labels and prices."""
    with patch("app.modules.stock.service.get_candle_data", new=AsyncMock(return_value=MOCK_CANDLE)):
        resp = await client.get("/api/stock/history/AAPL")
    assert resp.status_code == 200
    data = resp.json()
    assert "labels" in data
    assert "prices" in data
    assert len(data["prices"]) == 3


async def test_stock_history_unknown_symbol(client):
    """GET /api/stock/history/ZZZZZ returns 404 when no data."""
    with patch("app.modules.stock.service.get_candle_data", new=AsyncMock(return_value=None)):
        resp = await client.get("/api/stock/history/ZZZZZ")
    assert resp.status_code == 404
    assert "error" in resp.json()


async def test_stock_history_accepts_period_param(client):
    """Period query param is forwarded to the service."""
    with patch("app.modules.stock.service.get_candle_data", new=AsyncMock(return_value=MOCK_CANDLE)) as mock:
        resp = await client.get("/api/stock/history/AAPL?period=1Y")
    assert resp.status_code == 200
    mock.assert_called_once_with("AAPL", "1Y")


# ── /api/stock/search ─────────────────────────────────────────────────────────

async def test_stock_search_returns_results(client):
    """GET /api/stock/search?q=aapl returns symbol matches."""
    with patch("app.modules.stock.service.search_symbols", new=AsyncMock(return_value=MOCK_SEARCH_RESULTS)):
        resp = await client.get("/api/stock/search?q=aapl")
    assert resp.status_code == 200
    assert "results" in resp.json()
    assert resp.json()["results"][0]["symbol"] == "AAPL"


async def test_stock_search_empty_query(client):
    """GET /api/stock/search with no q returns empty results."""
    resp = await client.get("/api/stock/search")
    assert resp.status_code == 200
    assert resp.json()["results"] == []


async def test_stock_search_long_query_rejected(client):
    """Query over 50 chars returns empty results (length guard)."""
    long_q = "A" * 51
    with patch("app.modules.stock.service.search_symbols", new=AsyncMock(return_value=[])):
        resp = await client.get(f"/api/stock/search?q={long_q}")
    assert resp.status_code == 200
    assert resp.json()["results"] == []


# ── /api/market/ticker ────────────────────────────────────────────────────────

async def test_market_ticker_returns_list(client):
    """GET /api/market/ticker returns a list of symbol/price/change dicts."""
    with patch("app.modules.stock.service.get_ticker_data", new=AsyncMock(return_value=MOCK_TICKER)):
        resp = await client.get("/api/market/ticker")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["symbol"] == "SPY"
    assert "price" in data[0]
    assert "change" in data[0]


# ── /api/portfolio ────────────────────────────────────────────────────────────

async def test_portfolio_requires_auth(client):
    """GET /api/portfolio without a session redirects to /login."""
    resp = await client.get("/api/portfolio", follow_redirects=False)
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]


async def test_portfolio_returns_structure(client):
    """GET /api/portfolio for a logged-in user with no positions returns expected keys."""
    await _signup_and_login(client, "portfolio_api@test.com")
    with patch("app.modules.stock.service.get_candle_data", new=AsyncMock(return_value=None)):
        resp = await client.get("/api/portfolio")
    assert resp.status_code == 200
    data = resp.json()
    assert "unrealized" in data
    assert "realized" in data
    assert "avg_return" in data
    assert "spy_return" in data
    assert "total_picks" in data


async def test_portfolio_unrealized_position(client, db_session):
    """A favorited stock with a known buy price appears in unrealized positions."""
    from unittest.mock import AsyncMock, patch

    email = "portfolio_pos@test.com"
    await _signup_and_login(client, email)

    MOCK_QUOTE = {
        "symbol": "AAPL", "open": 170.0, "current": 180.0,
        "high": 181.0, "low": 169.0, "prev_close": 170.0, "percent_change": 5.88,
    }

    # Add a favorite (captures buy price via live quote)
    with patch("app.modules.stock.service.get_quote", new=AsyncMock(return_value=MOCK_QUOTE)):
        await client.post("/stock/favorite/AAPL", follow_redirects=False)

    # Fetch portfolio — current price is still MOCK_QUOTE
    with patch("app.modules.stock.service.get_quote",    new=AsyncMock(return_value=MOCK_QUOTE)), \
         patch("app.modules.stock.service.get_candle_data", new=AsyncMock(return_value=None)):
        resp = await client.get("/api/portfolio")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["unrealized"]) >= 1
    pos = data["unrealized"][0]
    assert pos["symbol"] == "AAPL"
    assert "return_pct" in pos


# ── /api/reddit/summary ───────────────────────────────────────────────────────

async def test_reddit_summary_returns_data(client):
    """GET /api/reddit/summary returns tickers and summary text."""
    # Patch the name the router imported directly, not the service module attribute.
    # reddit/router.py does `from app.modules.reddit.service import get_reddit_summary`,
    # so patching the service module has no effect on the router's local binding.
    with patch("app.modules.reddit.router.get_reddit_summary", new=AsyncMock(return_value=MOCK_REDDIT)):
        resp = await client.get("/api/reddit/summary?timeframe=day")
    assert resp.status_code == 200
    data = resp.json()
    assert "tickers" in data
    assert "summary" in data
    assert data["tickers"][0]["ticker"] == "NVDA"


async def test_reddit_summary_invalid_timeframe(client):
    """Invalid timeframe is coerced to 'day' — still returns 200."""
    with patch("app.modules.reddit.router.get_reddit_summary", new=AsyncMock(return_value=MOCK_REDDIT)):
        resp = await client.get("/api/reddit/summary?timeframe=invalid")
    assert resp.status_code == 200
