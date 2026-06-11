import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

import httpx

from app.cache import cache_get, cache_set
from app.config import settings

log = logging.getLogger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"
YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"

# ── Candle periods config (Yahoo Finance range/interval params) ───────────────
_CANDLE_PERIODS = {
    "1D": {"range": "1d",  "interval": "1h",  "ttl": 300,  "fmt": "time"},
    "1W": {"range": "5d",  "interval": "1d",  "ttl": 3600, "fmt": "short"},
    "1M": {"range": "1mo", "interval": "1d",  "ttl": 3600, "fmt": "short"},
    "6M": {"range": "6mo", "interval": "1wk", "ttl": 3600, "fmt": "long"},
    "1Y": {"range": "1y",  "interval": "1wk", "ttl": 3600, "fmt": "long"},
}

# Symbols shown in the live navbar ticker
_TICKER_SYMBOLS = ["SPY", "QQQ", "NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "AMD"]

# Curated list for top-movers calculation (no paid endpoint needed)
TOP_SYMBOLS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
    "META", "TSLA", "JPM", "V", "UNH",
    "AMD", "INTC", "BA", "DIS", "NFLX",
    "PLTR", "SOFI", "GME", "AMC", "RIVN",
]


async def get_quote(symbol: str) -> dict | None:
    symbol = symbol.upper()
    cached = await cache_get(f"stock:quote:{symbol}")
    if cached:
        return cached

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{FINNHUB_BASE}/quote",
                params={"symbol": symbol, "token": settings.FINNHUB_API_KEY},
                timeout=5.0,
            )
            resp.raise_for_status()
            data = resp.json()

            if not data.get("o") and not data.get("c"):
                return None

            pc = data.get("pc") or 1
            pct = round(((data.get("c", 0) - pc) / pc) * 100, 2)

            result = {
                "symbol": symbol,
                "open": data.get("o"),
                "current": data.get("c"),
                "high": data.get("h"),
                "low": data.get("l"),
                "prev_close": data.get("pc"),
                "percent_change": pct,
            }
            await cache_set(f"stock:quote:{symbol}", result, ttl=60)
            return result
        except Exception as e:
            log.warning("get_quote failed for %s: %s", symbol, e)
            return None


async def get_peers(symbol: str) -> list[str]:
    symbol = symbol.upper()
    cached = await cache_get(f"stock:peers:{symbol}")
    if cached:
        return cached

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{FINNHUB_BASE}/stock/peers",
                params={"symbol": symbol, "token": settings.FINNHUB_API_KEY},
                timeout=5.0,
            )
            resp.raise_for_status()
            peers = [p for p in resp.json() if p != symbol][:6]
            await cache_set(f"stock:peers:{symbol}", peers, ttl=3600)
            return peers
        except Exception as e:
            log.warning("get_peers failed for %s: %s", symbol, e)
            return []


async def _fetch_quote_raw(client: httpx.AsyncClient, symbol: str) -> dict | None:
    """Low-level single Finnhub quote fetch (no cache) — used by batch operations."""
    try:
        resp = await client.get(
            f"{FINNHUB_BASE}/quote",
            params={"symbol": symbol, "token": settings.FINNHUB_API_KEY},
            timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("c") and data.get("pc"):
            pct = round(((data["c"] - data["pc"]) / data["pc"]) * 100, 2)
            return {"symbol": symbol, "current": data["c"], "percent_change": pct}
    except Exception as e:
        log.warning("_fetch_quote_raw failed for %s: %s", symbol, e)
    return None


async def get_top_movers() -> dict:
    """
    Build top gainers/losers from a curated symbol list using concurrent fetches.
    Cached for 5 minutes.

    Note: Finnhub's free tier has no dedicated market movers endpoint,
    so we batch-fetch a curated set and sort by % change.
    """
    cached = await cache_get("market:top_movers")
    if cached:
        return cached

    async with httpx.AsyncClient() as client:
        tasks = [_fetch_quote_raw(client, sym) for sym in TOP_SYMBOLS]
        raw = await asyncio.gather(*tasks)

    quotes = [q for q in raw if q is not None]
    quotes.sort(key=lambda x: x["percent_change"], reverse=True)
    result = {"gainers": quotes[:5], "losers": list(reversed(quotes[-5:]))}
    await cache_set("market:top_movers", result, ttl=300)
    return result


async def get_candle_data(symbol: str, period: str = "1M") -> dict | None:
    symbol = symbol.upper()
    if period not in _CANDLE_PERIODS:
        period = "1M"

    cache_key = f"stock:candle:{symbol}:{period}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    cfg = _CANDLE_PERIODS[period]

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; StockApp/1.0)",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(headers=headers) as client:
        try:
            resp = await client.get(
                f"{YAHOO_BASE}/{symbol}",
                params={
                    "range": cfg["range"],
                    "interval": cfg["interval"],
                    "includePrePost": "false",
                },
                timeout=8.0,
            )
            resp.raise_for_status()
            body = resp.json()

            chart_result = body.get("chart", {}).get("result")
            if not chart_result:
                return None

            data = chart_result[0]
            timestamps = data.get("timestamp", [])
            closes = data.get("indicators", {}).get("quote", [{}])[0].get("close", [])

            if not timestamps or not closes:
                return None

            # Filter out null values (market closures / pre/post market gaps)
            pairs = [(ts, c) for ts, c in zip(timestamps, closes) if c is not None]
            if not pairs:
                return None

            def fmt_label(ts: int) -> str:
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                if cfg["fmt"] == "time":
                    # Convert to ET so x-axis shows market hours (9am–4pm), not UTC
                    return dt.astimezone(_ET).strftime("%-I%p").lower()  # e.g. "10am"
                elif cfg["fmt"] == "long":
                    return dt.strftime("%b '%y")           # e.g. "Jun '26"
                else:
                    return dt.strftime("%b %d")            # e.g. "Jun 09"

            result = {
                "labels": [fmt_label(ts) for ts, _ in pairs],
                "prices": [c for _, c in pairs],
                "period": period,
            }
            await cache_set(cache_key, result, ttl=cfg["ttl"])
            return result
        except Exception as e:
            log.warning("get_candle_data failed for %s/%s: %s", symbol, period, e)
            return None


async def search_symbols(query: str) -> list[dict]:
    if not query:
        return []
    if len(query) > 50:
        return []

    cache_key = f"stock:search:{query.upper()}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    # Yahoo Finance search returns US-listed ADRs (HMC, TM, etc.) that Finnhub
    # search misses — Finnhub only surfaces primary-exchange listings (Tokyo, etc.).
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; StockApp/1.0)",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(headers=headers) as client:
        try:
            resp = await client.get(
                YAHOO_SEARCH_URL,
                params={"q": query, "quotesCount": 10, "newsCount": 0, "enableFuzzyQuery": "false"},
                timeout=5.0,
            )
            resp.raise_for_status()
            quotes = resp.json().get("quotes", [])
            results = [
                {"symbol": r["symbol"], "name": r.get("shortname") or r.get("longname") or r["symbol"]}
                for r in quotes
                if r.get("quoteType") == "EQUITY" and "." not in r.get("symbol", "")
            ][:7]
            await cache_set(cache_key, results, ttl=3600)
            return results
        except Exception as e:
            log.warning("search_symbols failed for %r: %s", query, e)
            return []


async def get_ticker_data() -> list[dict]:
    cached = await cache_get("market:ticker_bar")
    if cached:
        return cached

    quotes = await asyncio.gather(*[get_quote(sym) for sym in _TICKER_SYMBOLS])
    results = [
        {"symbol": sym, "price": q["current"], "change": q["percent_change"]}
        for sym, q in zip(_TICKER_SYMBOLS, quotes)
        if q is not None
    ]

    await cache_set("market:ticker_bar", results, ttl=60)
    return results


async def get_favorites_with_prices(symbols: list[str]) -> list[dict]:
    if not symbols:
        return []
    quotes = await asyncio.gather(*[get_quote(sym) for sym in symbols])
    return [q for q in quotes if q is not None]


async def get_kpi_data() -> tuple[str, str, dict | None]:
    """
    Return (trending_symbol, market_mood_label, biggest_mover).
    Extracted from router layer to keep business logic in the service.
    """
    from app.modules.reddit import service as reddit_svc  # local import avoids circular

    trending = "—"
    market_mood = "Mixed"
    biggest_mover = None

    try:
        rdata = await reddit_svc.get_reddit_summary("day")
        if rdata.get("tickers"):
            trending = rdata["tickers"][0]["ticker"]
    except Exception as e:
        log.warning("KPI trending fetch failed: %s", e)

    try:
        movers = await get_top_movers()
        gainers = movers.get("gainers", [])
        losers = movers.get("losers", [])
        if len(gainers) > len(losers):
            market_mood = "Bullish"
        elif len(losers) > len(gainers):
            market_mood = "Bearish"
        else:
            market_mood = "Mixed"
        if gainers:
            top = gainers[0]
            biggest_mover = {"symbol": top["symbol"], "change": top["percent_change"]}
    except Exception as e:
        log.warning("KPI movers fetch failed: %s", e)

    return trending, market_mood, biggest_mover
