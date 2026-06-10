"""
Repository (DB layer) unit tests.

These tests call repository functions directly against the test database,
bypassing HTTP entirely. This isolates data-layer logic from route/template
concerns and gives precise failure signals when something breaks at the DB level.
"""
import pytest

from sqlalchemy import select

from app.modules.auth.service import hash_password
from app.modules.user import repository as repo
from app.modules.user.models import FavoriteHistory, User, UserFavorite

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _make_user(db, email="repo_test@test.com", password="testpass123") -> User:
    """Create and return a persisted user."""
    return await repo.create_user(db, email, hash_password(password))


# ── create_user ───────────────────────────────────────────────────────────────

async def test_create_user_persists(db_session):
    """create_user writes a row and returns the User with an assigned ID."""
    user = await _make_user(db_session, "create@test.com")
    assert user.id is not None
    assert user.email == "create@test.com"
    assert user.hashed_password != "testpass123"  # bcrypt-hashed, never plaintext


async def test_create_user_email_lowercased_by_route_not_repo(db_session):
    """Repo stores exactly what it's given — normalisation is the caller's job."""
    user = await _make_user(db_session, "MixedCase@test.com")
    assert user.email == "MixedCase@test.com"


# ── get_by_email ──────────────────────────────────────────────────────────────

async def test_get_by_email_found(db_session):
    await _make_user(db_session, "byemail@test.com")
    found = await repo.get_by_email(db_session, "byemail@test.com")
    assert found is not None
    assert found.email == "byemail@test.com"


async def test_get_by_email_not_found(db_session):
    result = await repo.get_by_email(db_session, "nobody@test.com")
    assert result is None


# ── get_by_id ─────────────────────────────────────────────────────────────────

async def test_get_by_id_found(db_session):
    user = await _make_user(db_session, "byid@test.com")
    found = await repo.get_by_id(db_session, user.id)
    assert found is not None
    assert found.id == user.id


async def test_get_by_id_loads_relationships(db_session):
    """get_by_id eager-loads favorites and searches so callers don't lazy-load."""
    user = await _make_user(db_session, "relations@test.com")
    await repo.add_favorite(db_session, user.id, "AAPL")
    found = await repo.get_by_id(db_session, user.id)
    # Accessing .favorites must not raise MissingGreenlet (lazy load in async)
    assert isinstance(found.favorites, list)
    assert found.favorites[0].symbol == "AAPL"


async def test_get_by_id_not_found(db_session):
    result = await repo.get_by_id(db_session, 999999)
    assert result is None


# ── save_search / get_recent_searches ─────────────────────────────────────────

async def test_save_search_persists(db_session):
    user = await _make_user(db_session, "search_save@test.com")
    search = await repo.save_search(
        db_session,
        user_id=user.id,
        symbol="TSLA",
        open_price=200.0,
        current_price=210.0,
        high_price=215.0,
        low_price=198.0,
        percent_change=5.0,
    )
    assert search.id is not None
    assert search.symbol == "TSLA"
    assert search.current_price == 210.0


async def test_get_recent_searches_ordering(db_session):
    """Most recent search appears first."""
    user = await _make_user(db_session, "search_order@test.com")
    for sym in ("AAPL", "MSFT", "NVDA"):
        await repo.save_search(
            db_session, user.id, sym,
            open_price=100.0, current_price=100.0,
            high_price=100.0, low_price=100.0, percent_change=0.0,
        )
    searches = await repo.get_recent_searches(db_session, user.id, limit=5)
    assert searches[0].symbol == "NVDA"  # last inserted = most recent


async def test_get_recent_searches_respects_limit(db_session):
    user = await _make_user(db_session, "search_limit@test.com")
    for sym in ("A", "B", "C", "D", "E"):
        await repo.save_search(
            db_session, user.id, sym,
            open_price=1.0, current_price=1.0,
            high_price=1.0, low_price=1.0, percent_change=0.0,
        )
    searches = await repo.get_recent_searches(db_session, user.id, limit=3)
    assert len(searches) == 3


async def test_get_recent_searches_empty(db_session):
    user = await _make_user(db_session, "search_empty@test.com")
    searches = await repo.get_recent_searches(db_session, user.id)
    assert searches == []


# ── add_favorite ──────────────────────────────────────────────────────────────

async def test_add_favorite_persists_with_price(db_session):
    user = await _make_user(db_session, "fav_add@test.com")
    fav = await repo.add_favorite(db_session, user.id, "AAPL", price_at_add=175.0)
    assert fav is not None
    assert fav.symbol == "AAPL"
    assert fav.price_at_add == 175.0


async def test_add_favorite_idempotent(db_session):
    """Adding the same symbol twice returns None on the second call (no duplicate)."""
    user = await _make_user(db_session, "fav_idem@test.com")
    first  = await repo.add_favorite(db_session, user.id, "MSFT", price_at_add=300.0)
    second = await repo.add_favorite(db_session, user.id, "MSFT", price_at_add=305.0)
    assert first is not None
    assert second is None  # duplicate rejected silently

    # Only one row in DB
    result = await db_session.execute(
        select(UserFavorite).where(
            UserFavorite.user_id == user.id,
            UserFavorite.symbol == "MSFT",
        )
    )
    assert len(result.scalars().all()) == 1


async def test_add_favorite_uppercases_symbol(db_session):
    user = await _make_user(db_session, "fav_case@test.com")
    fav = await repo.add_favorite(db_session, user.id, "nvda", price_at_add=900.0)
    assert fav.symbol == "NVDA"


async def test_add_favorite_without_price(db_session):
    """price_at_add is optional — favorites added before P&L tracking still work."""
    user = await _make_user(db_session, "fav_noprice@test.com")
    fav = await repo.add_favorite(db_session, user.id, "AMD")
    assert fav is not None
    assert fav.price_at_add is None


# ── remove_favorite ───────────────────────────────────────────────────────────

async def test_remove_favorite_deletes_row(db_session):
    user = await _make_user(db_session, "fav_del@test.com")
    await repo.add_favorite(db_session, user.id, "AAPL", price_at_add=170.0)
    removed = await repo.remove_favorite(db_session, user.id, "AAPL", current_price=180.0)
    assert removed is True

    result = await db_session.execute(
        select(UserFavorite).where(
            UserFavorite.user_id == user.id,
            UserFavorite.symbol == "AAPL",
        )
    )
    assert result.scalar_one_or_none() is None


async def test_remove_favorite_writes_history(db_session):
    """Removing a favorite creates a FavoriteHistory row with computed return_pct."""
    user = await _make_user(db_session, "fav_hist@test.com")
    await repo.add_favorite(db_session, user.id, "AAPL", price_at_add=100.0)
    await repo.remove_favorite(db_session, user.id, "AAPL", current_price=110.0)

    result = await db_session.execute(
        select(FavoriteHistory).where(
            FavoriteHistory.user_id == user.id,
            FavoriteHistory.symbol == "AAPL",
        )
    )
    hist = result.scalar_one()
    assert hist.buy_price == 100.0
    assert hist.sell_price == 110.0
    assert hist.return_pct == pytest.approx(10.0, abs=0.01)


async def test_remove_favorite_history_no_price(db_session):
    """If no buy price was recorded, return_pct is None (not a crash)."""
    user = await _make_user(db_session, "fav_noprice_hist@test.com")
    await repo.add_favorite(db_session, user.id, "GME")  # no price_at_add
    await repo.remove_favorite(db_session, user.id, "GME", current_price=20.0)

    result = await db_session.execute(
        select(FavoriteHistory).where(
            FavoriteHistory.user_id == user.id,
            FavoriteHistory.symbol == "GME",
        )
    )
    hist = result.scalar_one()
    assert hist.return_pct is None


async def test_remove_favorite_not_found(db_session):
    """Removing a symbol that was never favorited returns False gracefully."""
    user = await _make_user(db_session, "fav_missing@test.com")
    removed = await repo.remove_favorite(db_session, user.id, "ZZZZZ")
    assert removed is False


# ── get_portfolio_data ────────────────────────────────────────────────────────

async def test_get_portfolio_data_empty(db_session):
    user = await _make_user(db_session, "port_empty@test.com")
    data = await repo.get_portfolio_data(db_session, user.id)
    assert data["open"] == []
    assert data["closed"] == []


async def test_get_portfolio_data_open_positions(db_session):
    """Active favorites with price_at_add appear in open positions."""
    user = await _make_user(db_session, "port_open@test.com")
    await repo.add_favorite(db_session, user.id, "AAPL", price_at_add=170.0)
    await repo.add_favorite(db_session, user.id, "MSFT", price_at_add=300.0)

    data = await repo.get_portfolio_data(db_session, user.id)
    symbols = {p.symbol for p in data["open"]}
    assert symbols == {"AAPL", "MSFT"}
    assert data["closed"] == []


async def test_get_portfolio_data_closed_positions(db_session):
    """Removed favorites appear in closed positions after remove_favorite."""
    user = await _make_user(db_session, "port_closed@test.com")
    await repo.add_favorite(db_session, user.id, "TSLA", price_at_add=200.0)
    await repo.remove_favorite(db_session, user.id, "TSLA", current_price=220.0)

    data = await repo.get_portfolio_data(db_session, user.id)
    assert data["open"] == []
    assert len(data["closed"]) == 1
    assert data["closed"][0].symbol == "TSLA"
    assert data["closed"][0].return_pct == pytest.approx(10.0, abs=0.01)


async def test_get_portfolio_data_mixed(db_session):
    """Open and closed positions coexist correctly."""
    user = await _make_user(db_session, "port_mixed@test.com")
    await repo.add_favorite(db_session, user.id, "AAPL", price_at_add=150.0)  # keep open
    await repo.add_favorite(db_session, user.id, "GME",  price_at_add=30.0)   # will close
    await repo.remove_favorite(db_session, user.id, "GME", current_price=25.0)

    data = await repo.get_portfolio_data(db_session, user.id)
    assert len(data["open"])   == 1
    assert len(data["closed"]) == 1
    assert data["open"][0].symbol   == "AAPL"
    assert data["closed"][0].symbol == "GME"
    assert data["closed"][0].return_pct == pytest.approx(-16.67, abs=0.01)


async def test_get_portfolio_data_excludes_no_price_favorites(db_session):
    """Favorites added without a buy price don't appear in open positions (can't calc P&L)."""
    user = await _make_user(db_session, "port_nopr@test.com")
    await repo.add_favorite(db_session, user.id, "AMD")  # no price_at_add
    data = await repo.get_portfolio_data(db_session, user.id)
    assert data["open"] == []


# ── update_totp ───────────────────────────────────────────────────────────────

async def test_update_totp_enable(db_session):
    """update_totp sets the secret and marks 2FA enabled."""
    user = await _make_user(db_session, "totp_on@test.com")
    assert user.two_factor_enabled is False

    await repo.update_totp(db_session, user.id, secret="JBSWY3DPEHPK3PXP", enabled=True)

    refreshed = await repo.get_by_id(db_session, user.id)
    assert refreshed.two_factor_enabled is True
    assert refreshed.totp_secret == "JBSWY3DPEHPK3PXP"


async def test_update_totp_disable(db_session):
    """update_totp clears the secret and marks 2FA disabled."""
    user = await _make_user(db_session, "totp_off@test.com")
    await repo.update_totp(db_session, user.id, secret="SOMESECRET", enabled=True)
    await repo.update_totp(db_session, user.id, secret=None, enabled=False)

    refreshed = await repo.get_by_id(db_session, user.id)
    assert refreshed.two_factor_enabled is False
    assert refreshed.totp_secret is None
