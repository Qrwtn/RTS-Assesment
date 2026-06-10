from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.limiter import limiter
from app.modules.reddit.service import get_reddit_summary

router = APIRouter()


@router.get("/api/reddit/summary")
@limiter.limit("10/minute")
async def reddit_summary(request: Request, timeframe: str = "day"):
    """
    JSON endpoint for the Reddit sentiment widget.
    Called via fetch() from the dashboard — keeps initial page load fast.
    Rate-limited to protect ApeWisdom API quota.
    """
    data = await get_reddit_summary(timeframe)
    return JSONResponse(data)
