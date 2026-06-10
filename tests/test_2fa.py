"""
Two-factor authentication flow tests.

Covers:
  - 2FA login: page guard, invalid code, valid code
  - Settings: setup QR generation, enable (bad code / good code), disable (bad / good)
"""
import pytest
import pyotp
from unittest.mock import patch

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _signup_and_login(client, email, password="securepass123"):
    await client.post("/signup", data={"email": email, "password": password}, follow_redirects=False)
    await client.post("/login",  data={"email": email, "password": password}, follow_redirects=False)


# ── /login/2fa page guard ─────────────────────────────────────────────────────

async def test_2fa_page_redirects_without_pending_session(client):
    """/login/2fa with no pending_2fa_user_id redirects to /login."""
    resp = await client.get("/login/2fa", follow_redirects=False)
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]


# ── 2FA login flow ────────────────────────────────────────────────────────────

async def test_2fa_login_invalid_code(client, db_session):
    """User with 2FA enabled gets 401 on a bad TOTP code."""
    from app.modules.user import repository as user_repo

    email = "totp_bad@test.com"
    await _signup_and_login(client, email)

    # Enable TOTP directly in DB
    secret = pyotp.random_base32()
    result = await db_session.execute(
        __import__("sqlalchemy").select(
            __import__("app.modules.user.models", fromlist=["User"]).User
        ).where(
            __import__("app.modules.user.models", fromlist=["User"]).User.email == email
        )
    )
    user = result.scalar_one()
    await user_repo.update_totp(db_session, user.id, secret, enabled=True)
    await db_session.commit()

    # Log in again — should land on 2FA page
    resp = await client.post(
        "/login",
        data={"email": email, "password": "securepass123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/login/2fa" in resp.headers["location"]

    # Submit a wrong code
    resp = await client.post(
        "/login/2fa",
        data={"code": "000000"},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert b"invalid" in resp.content.lower()


async def test_2fa_login_valid_code(client, db_session):
    """User with 2FA enabled can log in with a correct TOTP code."""
    from app.modules.user import repository as user_repo
    from sqlalchemy import select
    from app.modules.user.models import User

    email = "totp_good@test.com"
    await _signup_and_login(client, email)

    secret = pyotp.random_base32()
    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    await user_repo.update_totp(db_session, user.id, secret, enabled=True)
    await db_session.commit()

    # Trigger the 2FA redirect
    await client.post(
        "/login",
        data={"email": email, "password": "securepass123"},
        follow_redirects=False,
    )

    # Submit the correct live TOTP code
    code = pyotp.TOTP(secret).now()
    resp = await client.post(
        "/login/2fa",
        data={"code": code},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"


# ── Settings: 2FA setup ───────────────────────────────────────────────────────

async def test_settings_security_page_loads(client):
    """GET /settings/security returns 200 for an authenticated user."""
    await _signup_and_login(client, "settings_page@test.com")
    resp = await client.get("/settings/security")
    assert resp.status_code == 200
    assert b"Two-Factor" in resp.content or b"2FA" in resp.content or b"Security" in resp.content


async def test_2fa_setup_generates_qr(client):
    """POST /settings/2fa/setup returns a QR code image for the user."""
    await _signup_and_login(client, "setup_qr@test.com")
    resp = await client.post("/settings/2fa/setup", follow_redirects=False)
    assert resp.status_code == 200
    # QR code is embedded as base64 image
    assert b"data:image/png;base64" in resp.content or b"qr_code" in resp.content.lower()


async def test_2fa_enable_wrong_code(client):
    """POST /settings/2fa/enable with a bad code returns 400."""
    await _signup_and_login(client, "enable_bad@test.com")
    # First generate the secret (stores it in session)
    await client.post("/settings/2fa/setup")
    # Submit a wrong code
    resp = await client.post(
        "/settings/2fa/enable",
        data={"code": "000000"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert b"invalid" in resp.content.lower()


async def test_2fa_enable_correct_code(client, db_session):
    """POST /settings/2fa/enable with the correct code enables 2FA."""

    email = "enable_good@test.com"
    await _signup_and_login(client, email)

    # Generate QR (stores pending_totp_secret in session)
    await client.post("/settings/2fa/setup")

    # Grab the secret that was stored in the session via the response
    # We inject a known secret by patching pyotp.random_base32
    secret = pyotp.random_base32()
    with patch("pyotp.random_base32", return_value=secret):
        await client.post("/settings/2fa/setup")

    code = pyotp.TOTP(secret).now()
    resp = await client.post(
        "/settings/2fa/enable",
        data={"code": code},
        follow_redirects=False,
    )
    # Should either succeed (200 with success message) or redirect
    assert resp.status_code in (200, 303)
    if resp.status_code == 200:
        assert b"enabled" in resp.content.lower() or b"success" in resp.content.lower()


# ── Settings: 2FA disable ─────────────────────────────────────────────────────

async def test_2fa_disable_wrong_code(client, db_session):
    """POST /settings/2fa/disable with a bad code returns 400."""
    from app.modules.user import repository as user_repo
    from sqlalchemy import select
    from app.modules.user.models import User

    email = "disable_bad@test.com"
    await _signup_and_login(client, email)

    # Enable 2FA in DB directly
    secret = pyotp.random_base32()
    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    await user_repo.update_totp(db_session, user.id, secret, enabled=True)
    await db_session.commit()

    resp = await client.post(
        "/settings/2fa/disable",
        data={"code": "000000"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert b"invalid" in resp.content.lower()


async def test_2fa_disable_correct_code(client, db_session):
    """POST /settings/2fa/disable with a correct code disables 2FA."""
    from app.modules.user import repository as user_repo
    from sqlalchemy import select
    from app.modules.user.models import User

    email = "disable_good@test.com"
    await _signup_and_login(client, email)

    secret = pyotp.random_base32()
    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    await user_repo.update_totp(db_session, user.id, secret, enabled=True)
    await db_session.commit()

    code = pyotp.TOTP(secret).now()
    resp = await client.post(
        "/settings/2fa/disable",
        data={"code": code},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert b"disabled" in resp.content.lower() or b"success" in resp.content.lower()
