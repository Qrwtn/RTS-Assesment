import logging

import anthropic
import httpx

from app.cache import cache_get, cache_set
from app.config import settings

log = logging.getLogger(__name__)

_TTL = {"day": 1800, "week": 7200}  # 30 min / 2 hr


async def get_reddit_summary(timeframe: str = "day") -> dict:
    """
    Fetch most-mentioned tickers from ApeWisdom (r/wallstreetbets + r/stocks)
    and generate an AI narrative summary using Claude.
    Full result cached to avoid repeated LLM calls.
    """
    if timeframe not in ("day", "week"):
        timeframe = "day"

    cached = await cache_get(f"reddit:summary:{timeframe}")
    if cached:
        return cached

    tickers = await _fetch_apewisdom()
    summary = await _generate_summary(tickers, timeframe) if tickers else "Reddit data unavailable."

    result = {"summary": summary, "tickers": tickers[:10], "timeframe": timeframe}
    await cache_set(f"reddit:summary:{timeframe}", result, ttl=_TTL[timeframe])
    return result


async def _fetch_apewisdom() -> list[dict]:
    merged: dict[str, dict] = {}

    async with httpx.AsyncClient(timeout=10.0) as client:
        for sub in ("wallstreetbets", "stocks"):
            try:
                resp = await client.get(
                    f"https://apewisdom.io/api/v1.0/filter/{sub}",
                    headers={"User-Agent": "StockApp/1.0"},
                )
                if resp.status_code != 200:
                    continue
                for item in resp.json().get("results", [])[:15]:
                    ticker = item.get("ticker", "")
                    if not ticker or len(ticker) > 5:
                        continue
                    if ticker in merged:
                        merged[ticker]["mentions"] += item.get("mentions", 0)
                    else:
                        merged[ticker] = {
                            "ticker": ticker,
                            "name": item.get("name", ticker),
                            "mentions": item.get("mentions", 0),
                            "mentions_24h_ago": item.get("mentions_24h_ago", 0),
                        }
            except Exception:
                continue

    return sorted(merged.values(), key=lambda x: x["mentions"], reverse=True)[:10]


async def _generate_summary(tickers: list[dict], timeframe: str) -> str:
    if not settings.ANTHROPIC_API_KEY or settings.AI_PROVIDER != "anthropic":
        log.warning("Anthropic not configured (AI_PROVIDER=%s), using fallback", settings.AI_PROVIDER)
        return _fallback_summary(tickers, timeframe)

    try:
        # Use AsyncAnthropic — sync client blocks the event loop in async FastAPI
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        period = "today" if timeframe == "day" else "this week"
        ticker_lines = "\n".join(
            f"- {t['ticker']} ({t['name']}): {t['mentions']} mentions"
            for t in tickers[:8]
        )
        msg = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": (
                    f"Based on Reddit activity {period} across r/wallstreetbets and r/stocks, "
                    f"here are the most mentioned stocks:\n{ticker_lines}\n\n"
                    "Write 2-3 sentences summarizing what retail investors are focused on. "
                    "Mention the top 2-3 tickers by name. Add a line break after each subject so its easier to read."
                    "Be neutral, concise, and factual. "
                    "Do not give investment advice."
                ),
            }],
        )
        return msg.content[0].text
    except Exception as e:
        log.error("Claude summary failed: %s", e)
        return _fallback_summary(tickers, timeframe)


def _fallback_summary(tickers: list[dict], timeframe: str) -> str:
    if not tickers:
        return "Reddit sentiment data is temporarily unavailable."
    top = ", ".join(t["ticker"] for t in tickers[:3])
    period = "today" if timeframe == "day" else "this week"
    return f"The most discussed stocks {period} include {top}. See mention counts below."
