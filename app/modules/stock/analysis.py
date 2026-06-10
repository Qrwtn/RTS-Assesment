"""
AI-powered stock analysis.

Synthesises analyst price targets (Yahoo Finance quoteSummary),
recent company news (Finnhub), and Reddit sentiment (ApeWisdom cache)
into a Claude Haiku narrative. Result is cached per-symbol for 1 hour.

Data flow:
  get_stock_analysis(symbol, quote)
    ├── _fetch_price_targets()  → Yahoo Finance quoteSummary
    ├── _fetch_company_news()   → Finnhub /company-news
    ├── _fetch_reddit_mentions()→ existing cached ApeWisdom data
    └── _generate_analysis()   → Claude Haiku (or fallback text)
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import anthropic
import httpx

from app.cache import cache_get, cache_set
from app.config import settings

log = logging.getLogger(__name__)

FINNHUB_BASE       = "https://finnhub.io/api/v1"
YAHOO_SUMMARY_BASE = "https://query1.finance.yahoo.com/v10/finance/quoteSummary"
_HEADERS           = {"User-Agent": "Mozilla/5.0 (compatible; StockApp/1.0)", "Accept": "application/json"}


async def get_stock_analysis(symbol: str, quote: dict | None = None) -> dict:
    """
    Return a full analysis dict for the symbol.
    Cached for 1 hour — analysis doesn't need to be real-time.
    """
    symbol = symbol.upper()
    cache_key = f"stock:analysis:{symbol}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    targets, news, reddit = await asyncio.gather(
        _fetch_price_targets(symbol),
        _fetch_company_news(symbol, days=7),
        _fetch_reddit_mentions(symbol),
        return_exceptions=True,
    )

    # Treat any failed fetch as a graceful None / []
    targets = targets if not isinstance(targets, Exception) else None
    news    = news    if not isinstance(news,    Exception) else []
    reddit  = reddit  if not isinstance(reddit,  Exception) else None

    summary = await _generate_analysis(symbol, quote, targets, news or [], reddit)

    result = {
        "symbol":       symbol,
        "summary":      summary,
        "targets":      targets,
        "news":         (news or [])[:5],
        "reddit":       reddit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    await cache_set(cache_key, result, ttl=3600)
    return result


# ── Data fetchers ─────────────────────────────────────────────────────────────

async def _fetch_price_targets(symbol: str) -> dict | None:
    """Analyst price targets + next earnings from Yahoo Finance quoteSummary."""
    try:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=8.0) as client:
            resp = await client.get(
                f"{YAHOO_SUMMARY_BASE}/{symbol}",
                params={"modules": "financialData,calendarEvents"},
            )
            resp.raise_for_status()
            body = resp.json()

        result_list = (body.get("quoteSummary") or {}).get("result") or []
        if not result_list:
            return None

        data      = result_list[0]
        financial = data.get("financialData") or {}
        calendar  = data.get("calendarEvents") or {}
        targets: dict = {}

        mean_raw = (financial.get("targetMeanPrice") or {}).get("raw")
        if mean_raw:
            targets["mean"]           = round(mean_raw, 2)
            targets["high"]           = round(((financial.get("targetHighPrice") or {}).get("raw") or mean_raw), 2)
            targets["low"]            = round(((financial.get("targetLowPrice")  or {}).get("raw") or mean_raw), 2)
            targets["analysts"]       = ((financial.get("numberOfAnalystOpinions") or {}).get("raw"))
            targets["recommendation"] = (financial.get("recommendationKey") or "").replace("_", " ").title()

        earnings_list = (calendar.get("earnings") or {}).get("earningsDate") or []
        if earnings_list:
            ts = (earnings_list[0] or {}).get("raw")
            if ts:
                targets["next_earnings"] = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %d, %Y")

        return targets if targets else None

    except Exception as e:
        log.warning("_fetch_price_targets failed for %s: %s", symbol, e)
        return None


async def _fetch_company_news(symbol: str, days: int = 7) -> list[dict]:
    """Recent company news headlines from Finnhub."""
    try:
        now       = datetime.now(timezone.utc)
        from_date = (now - timedelta(days=days)).strftime("%Y-%m-%d")
        to_date   = now.strftime("%Y-%m-%d")

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{FINNHUB_BASE}/company-news",
                params={
                    "symbol": symbol,
                    "from":   from_date,
                    "to":     to_date,
                    "token":  settings.FINNHUB_API_KEY,
                },
            )
            resp.raise_for_status()
            articles = resp.json()

        if not isinstance(articles, list):
            return []

        return [
            {
                "headline": a["headline"],
                "summary":  (a.get("summary") or "")[:200],
                "url":      a.get("url", "#"),
                "date":     datetime.fromtimestamp(a["datetime"], tz=timezone.utc).strftime("%b %d"),
            }
            for a in articles[:8]
            if a.get("headline") and a.get("datetime")
        ]

    except Exception as e:
        log.warning("_fetch_company_news failed for %s: %s", symbol, e)
        return []


async def _fetch_reddit_mentions(symbol: str) -> dict | None:
    """
    Look up this symbol in today's cached ApeWisdom data.
    Avoids a fresh API call — piggybacks on the existing KPI cache.
    """
    try:
        from app.modules.reddit.service import get_reddit_summary
        reddit_data = await get_reddit_summary("day")
        for ticker in reddit_data.get("tickers", []):
            if ticker["ticker"] == symbol:
                ago   = ticker.get("mentions_24h_ago", ticker["mentions"])
                delta = ticker["mentions"] - ago
                return {
                    "mentions": ticker["mentions"],
                    "trend":    "up" if delta > 0 else ("down" if delta < 0 else "flat"),
                    "delta":    abs(delta),
                }
        return None
    except Exception as e:
        log.warning("_fetch_reddit_mentions failed for %s: %s", symbol, e)
        return None


# ── Claude synthesis ──────────────────────────────────────────────────────────

async def _generate_analysis(
    symbol:  str,
    quote:   dict | None,
    targets: dict | None,
    news:    list[dict],
    reddit:  dict | None,
) -> str:
    if not settings.ANTHROPIC_API_KEY or settings.AI_PROVIDER != "anthropic":
        return _fallback_analysis(symbol, quote, targets, news)

    lines = [f"Stock: {symbol}"]

    if quote:
        lines.append(f"Current price: ${quote['current']:.2f} ({quote['percent_change']:+.2f}% today)")

    if quote and targets and targets.get("mean"):
        upside = round(((targets["mean"] - quote["current"]) / quote["current"]) * 100, 1)
        lines.append(
            f"Analyst consensus: {targets.get('recommendation', 'N/A')} | "
            f"Mean target: ${targets['mean']} ({upside:+.1f}% implied move) | "
            f"Range: ${targets.get('low', '?')}–${targets.get('high', '?')} "
            f"({targets.get('analysts', '?')} analysts)"
        )

    if targets and targets.get("next_earnings"):
        lines.append(f"Next earnings: {targets['next_earnings']}")

    if reddit:
        lines.append(
            f"Reddit mentions today: {reddit['mentions']} "
            f"({reddit['trend']} {reddit['delta']} vs yesterday)"
        )

    if news:
        lines.append("Recent headlines:")
        for a in news[:4]:
            lines.append(f"  [{a['date']}] {a['headline']}")

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        msg = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=230,
            messages=[{
                "role": "user",
                "content": (
                    f"You are a concise financial analyst assistant. "
                    f"Based on the data below, write 3–4 sentences about {symbol}. "
                    f"Cover: price vs analyst targets, retail sentiment if present, "
                    f"and the most relevant recent news or upcoming catalyst. "
                    f"Be factual and neutral. Do not give investment advice.\n\n"
                    + "\n".join(lines)
                ),
            }],
        )
        return msg.content[0].text
    except Exception as e:
        log.error("Claude analysis failed for %s: %s", symbol, e)
        return _fallback_analysis(symbol, quote, targets, news)


def _fallback_analysis(
    symbol:  str,
    quote:   dict | None,
    targets: dict | None,
    news:    list[dict],
) -> str:
    parts = []
    if quote and targets and targets.get("mean"):
        upside = round(((targets["mean"] - quote["current"]) / quote["current"]) * 100, 1)
        parts.append(
            f"{symbol} is trading at ${quote['current']:.2f}, "
            f"{upside:+.1f}% vs the analyst mean target of ${targets['mean']} "
            f"({targets.get('recommendation', 'N/A')}, {targets.get('analysts', '?')} analysts)."
        )
    if targets and targets.get("next_earnings"):
        parts.append(f"Next earnings: {targets['next_earnings']}.")
    if news:
        parts.append(f"Recent news: {news[0]['headline']}.")
    return " ".join(parts) if parts else f"Analysis data for {symbol} is currently unavailable."
