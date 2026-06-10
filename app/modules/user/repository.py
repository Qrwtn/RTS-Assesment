from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.user.models import User, StockSearch, UserFavorite, FavoriteHistory


async def get_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_by_id(db: AsyncSession, user_id: int) -> User | None:
    result = await db.execute(
        select(User)
        .where(User.id == user_id)
        .options(selectinload(User.favorites), selectinload(User.searches))
    )
    return result.scalar_one_or_none()


async def create_user(db: AsyncSession, email: str, hashed_password: str) -> User:
    user = User(email=email, hashed_password=hashed_password)
    db.add(user)
    await db.flush()
    return user


async def save_search(
    db: AsyncSession,
    user_id: int,
    symbol: str,
    open_price: float,
    current_price: float,
    high_price: float,
    low_price: float,
    percent_change: float,
) -> StockSearch:
    search = StockSearch(
        user_id=user_id,
        symbol=symbol,
        open_price=open_price,
        current_price=current_price,
        high_price=high_price,
        low_price=low_price,
        percent_change=percent_change,
    )
    db.add(search)
    await db.flush()
    return search


async def get_recent_searches(
    db: AsyncSession, user_id: int, limit: int = 5
) -> list[StockSearch]:
    result = await db.execute(
        select(StockSearch)
        .where(StockSearch.user_id == user_id)
        .order_by(StockSearch.searched_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def add_favorite(
    db: AsyncSession, user_id: int, symbol: str, price_at_add: float | None = None
) -> UserFavorite | None:
    sym = symbol.upper()
    existing = await db.execute(
        select(UserFavorite).where(
            UserFavorite.user_id == user_id, UserFavorite.symbol == sym
        )
    )
    if existing.scalar_one_or_none():
        return None
    fav = UserFavorite(user_id=user_id, symbol=sym, price_at_add=price_at_add)
    db.add(fav)
    await db.flush()
    return fav


async def remove_favorite(
    db: AsyncSession, user_id: int, symbol: str, current_price: float | None = None
) -> bool:
    """
    Remove the active favorite and write a FavoriteHistory record
    so the closed position is preserved for realized P&L calculation.
    """
    result = await db.execute(
        select(UserFavorite).where(
            UserFavorite.user_id == user_id, UserFavorite.symbol == symbol.upper()
        )
    )
    fav = result.scalar_one_or_none()
    if not fav:
        return False

    # Compute realized return if we have both prices
    return_pct: float | None = None
    if fav.price_at_add and current_price and fav.price_at_add > 0:
        return_pct = round(((current_price - fav.price_at_add) / fav.price_at_add) * 100, 2)

    history = FavoriteHistory(
        user_id=user_id,
        symbol=fav.symbol,
        buy_price=fav.price_at_add,
        sell_price=current_price,
        return_pct=return_pct,
        bought_at=fav.added_at,
        sold_at=datetime.now(timezone.utc),
    )
    db.add(history)
    await db.delete(fav)
    await db.flush()
    return True


async def get_portfolio_data(db: AsyncSession, user_id: int) -> dict:
    """
    Return all data needed for the paper portfolio widget:
      - open_positions: current favorites with price_at_add
      - closed_positions: FavoriteHistory rows (realized P&L)
    """
    open_result = await db.execute(
        select(UserFavorite)
        .where(UserFavorite.user_id == user_id, UserFavorite.price_at_add.isnot(None))
        .order_by(UserFavorite.added_at.asc())
    )
    open_positions = list(open_result.scalars().all())

    closed_result = await db.execute(
        select(FavoriteHistory)
        .where(FavoriteHistory.user_id == user_id, FavoriteHistory.return_pct.isnot(None))
        .order_by(FavoriteHistory.sold_at.desc())
    )
    closed_positions = list(closed_result.scalars().all())

    return {
        "open": open_positions,
        "closed": closed_positions,
    }


async def update_totp(
    db: AsyncSession, user_id: int, secret: str | None, enabled: bool
) -> None:
    user = await get_by_id(db, user_id)
    if user:
        user.totp_secret = secret
        user.two_factor_enabled = enabled
        await db.flush()
