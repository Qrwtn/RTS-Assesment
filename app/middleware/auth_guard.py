from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.modules.user import repository as user_repo
from app.modules.user.models import User


class NotAuthenticatedException(Exception):
    """Raised when a protected route is accessed without a valid session."""
    pass


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    FastAPI dependency for protected routes.
    Returns the authenticated User or raises NotAuthenticatedException,
    which main.py handles by redirecting to /login.
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise NotAuthenticatedException()

    user = await user_repo.get_by_id(db, user_id)
    if not user:
        request.session.clear()
        raise NotAuthenticatedException()

    return user
