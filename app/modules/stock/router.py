from fastapi import APIRouter, Depends, Form, Path, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.limiter import limiter
from app.middleware.csrf import csrf_protect
from app.middleware.auth_guard import get_current_user
from app.modules.stock import service as stock_svc
from app.modules.user import repository as user_repo
from app.modules.user.models import User
from app.templates import templates

# Must match id="star-form" in stock/partials/_star_button.html
_STAR_FORM_ID = "star-form"

router = APIRouter()


# ── Pages ─────────────────────────────────────────────────────────────────────

@router.get("/")
async def index(request: Request, db: AsyncSession = Depends(get_db)):
    if request.session.get("user_id"):
        return RedirectResponse("/dashboard", status_code=303)
    top_movers = await stock_svc.get_top_movers()
    trending, market_mood, _ = await stock_svc.get_kpi_data()
    return templates.TemplateResponse(
        request,
        "stock/dashboard.html",
        {"user": None, "top_movers": top_movers, "trending": trending, "market_mood": market_mood},
    )


@router.get("/dashboard")
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    fav_symbols = [f.symbol for f in current_user.favorites]
    favorites = await stock_svc.get_favorites_with_prices(fav_symbols)
    recent = await user_repo.get_recent_searches(db, current_user.id, limit=5)
    trending, market_mood, biggest_mover = await stock_svc.get_kpi_data()

    return templates.TemplateResponse(
        request,
        "stock/dashboard.html",
        {
            "user": current_user,
            "favorites": favorites,
            "recent_searches": recent,
            "result": None,
            "peers": [],
            "biggest_mover": biggest_mover,
            "trending": trending,
            "market_mood": market_mood,
        },
    )


@router.post("/stock/lookup")
@limiter.limit("10/minute")
async def stock_lookup(
    request: Request,
    symbol: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(csrf_protect),
):
    symbol = symbol.upper().strip()
    quote = await stock_svc.get_quote(symbol)

    fav_symbols = [f.symbol for f in current_user.favorites]
    favorites = await stock_svc.get_favorites_with_prices(fav_symbols)
    recent = await user_repo.get_recent_searches(db, current_user.id)
    trending, market_mood, biggest_mover = await stock_svc.get_kpi_data()

    if not quote:
        return templates.TemplateResponse(
            request,
            "stock/dashboard.html",
            {
                "user": current_user,
                "favorites": favorites,
                "recent_searches": recent,
                "result": None,
                "peers": [],
                "error": f"Symbol '{symbol}' not found. Please check and try again.",
                "biggest_mover": biggest_mover,
                "trending": trending,
                "market_mood": market_mood,
            },
            status_code=404,
        )

    await user_repo.save_search(
        db,
        user_id=current_user.id,
        symbol=symbol,
        open_price=quote.get("open", 0),
        current_price=quote.get("current", 0),
        high_price=quote.get("high", 0),
        low_price=quote.get("low", 0),
        percent_change=quote.get("percent_change", 0),
    )

    peers = await stock_svc.get_peers(symbol)

    return templates.TemplateResponse(
        request,
        "stock/dashboard.html",
        {
            "user": current_user,
            "favorites": favorites,
            "recent_searches": recent,
            "result": quote,
            "peers": peers,
            "is_favorited": symbol in fav_symbols,
            "biggest_mover": biggest_mover,
            "trending": trending,
            "market_mood": market_mood,
        },
    )


_SYMBOL_PATH = Path(..., pattern=r"^[A-Z]{1,10}$", description="Stock ticker symbol")


async def _favorites_fragment(
    request: Request,
    db: AsyncSession,
    user_id: int,
    hx_target: str,
    symbol: str,
    is_favorited: bool,
):
    """Return the appropriate HTML fragment based on the HTMX target element."""
    fav_rows = await user_repo.get_favorites_by_user(db, user_id)
    fav_symbols = [f.symbol for f in fav_rows]
    favorites = await stock_svc.get_favorites_with_prices(fav_symbols)

    # Quote card star → return new star button + OOB watchlist update
    if hx_target == _STAR_FORM_ID:
        return templates.TemplateResponse(
            request,
            "stock/partials/_star_and_watchlist.html",
            {"symbol": symbol, "is_favorited": is_favorited, "favorites": favorites},
        )

    # Watchlist x-button → return watchlist fragment
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request,
            "stock/partials/_watchlist_inner.html",
            {"favorites": favorites},
        )

    # Non-HTMX fallback (JS disabled etc.)
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/stock/favorite/{symbol}")
async def add_favorite(
    request: Request,
    symbol: str = _SYMBOL_PATH,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(csrf_protect),
):
    # Snapshot the current price so we can calculate P&L later
    quote = await stock_svc.get_quote(symbol)
    price = quote["current"] if quote else None
    await user_repo.add_favorite(db, current_user.id, symbol, price_at_add=price)
    hx_target = request.headers.get("HX-Target", "")
    return await _favorites_fragment(request, db, current_user.id, hx_target, symbol, is_favorited=True)


@router.post("/stock/unfavorite/{symbol}")
async def remove_favorite(
    request: Request,
    symbol: str = _SYMBOL_PATH,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(csrf_protect),
):
    # Snapshot exit price to record realized P&L in history
    quote = await stock_svc.get_quote(symbol)
    current_price = quote["current"] if quote else None
    await user_repo.remove_favorite(db, current_user.id, symbol, current_price=current_price)
    hx_target = request.headers.get("HX-Target", "")
    return await _favorites_fragment(request, db, current_user.id, hx_target, symbol, is_favorited=False)


# ── JSON API endpoints ────────────────────────────────────────────────────────
# These endpoints are intentionally unauthenticated: they serve our own
# client-side JS (fetch calls) which cannot include session cookies cross-origin.
# All endpoints are rate-limited via slowapi.
# If this app becomes multi-tenant or public, add optional auth here.

@router.get("/api/stock/analysis/{symbol}")
@limiter.limit("20/minute")
async def stock_analysis(request: Request, symbol: str = _SYMBOL_PATH):
    """
    AI analysis card: analyst targets + recent news + Reddit + Claude narrative.
    Unauthenticated — called by client-side JS after the quote card renders.
    Cached 1 hour per symbol so repeated lookups are fast and cheap.
    Rate-limited to protect Anthropic and Finnhub API spend.
    """
    from app.modules.stock.analysis import get_stock_analysis
    sym   = symbol.upper()
    quote = await stock_svc.get_quote(sym)
    data  = await get_stock_analysis(sym, quote=quote)
    return JSONResponse(data)


@router.get("/api/stock/history/{symbol}")
@limiter.limit("30/minute")
async def stock_history(request: Request, symbol: str = _SYMBOL_PATH, period: str = "1M"):
    data = await stock_svc.get_candle_data(symbol.upper(), period)
    if not data:
        return JSONResponse({"error": "Historical data unavailable"}, status_code=404)
    return JSONResponse(data)


@router.get("/api/stock/search")
@limiter.limit("30/minute")
async def stock_search_api(request: Request, q: str = ""):
    results = await stock_svc.search_symbols(q)
    return JSONResponse({"results": results})


@router.get("/api/market/ticker")
@limiter.limit("10/minute")
async def market_ticker(request: Request):
    data = await stock_svc.get_ticker_data()
    return JSONResponse(data)


@router.get("/api/portfolio")
async def portfolio_api(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns the user's paper portfolio: open (unrealized) + closed (realized) positions
    with current prices fetched live, plus SPY benchmark return for comparison.
    """
    import asyncio
    from app.modules.user import repository as user_repo_local

    portfolio = await user_repo_local.get_portfolio_data(db, current_user.id)
    open_pos = portfolio["open"]
    closed_pos = portfolio["closed"]

    # Fetch live prices for all open positions concurrently
    live_quotes = await asyncio.gather(*[
        stock_svc.get_quote(p.symbol) for p in open_pos
    ])

    unrealized = []
    for pos, quote in zip(open_pos, live_quotes):
        if not quote or not pos.price_at_add:
            continue
        ret = round(((quote["current"] - pos.price_at_add) / pos.price_at_add) * 100, 2)
        unrealized.append({
            "symbol": pos.symbol,
            "buy_price": pos.price_at_add,
            "current_price": quote["current"],
            "return_pct": ret,
            "bought_at": pos.added_at.isoformat(),
        })

    realized = [
        {
            "symbol": p.symbol,
            "buy_price": p.buy_price,
            "sell_price": p.sell_price,
            "return_pct": p.return_pct,
            "bought_at": p.bought_at.isoformat() if p.bought_at else None,
            "sold_at": p.sold_at.isoformat() if p.sold_at else None,
        }
        for p in closed_pos
    ]

    # Equal-weight blended return across all positions (open + closed)
    all_returns = [p["return_pct"] for p in unrealized] + [p["return_pct"] for p in realized]
    avg_return = round(sum(all_returns) / len(all_returns), 2) if all_returns else None

    # SPY benchmark — use the same Yahoo Finance history endpoint
    spy_return = None
    if open_pos and current_user.created_at:
        spy_data = await stock_svc.get_candle_data("SPY", "1Y")
        if spy_data and spy_data.get("prices") and len(spy_data["prices"]) >= 2:
            spy_prices = spy_data["prices"]
            spy_return = round(((spy_prices[-1] - spy_prices[0]) / spy_prices[0]) * 100, 2)

    return JSONResponse({
        "unrealized": unrealized,
        "realized": realized,
        "avg_return": avg_return,
        "spy_return": spy_return,
        "total_picks": len(all_returns),
    })


