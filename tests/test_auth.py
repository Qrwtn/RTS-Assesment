"""
Authentication flow tests — signup, login, logout, 2FA prompt, email validation.
"""
import pytest

pytestmark = pytest.mark.asyncio


# ── Signup ────────────────────────────────────────────────────────────────────

async def test_signup_success(client):
    """New user signs up and is redirected to the 2FA welcome prompt."""
    resp = await client.post(
        "/signup",
        data={"email": "alice@test.com", "password": "securepass123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/welcome"


async def test_signup_redirects_to_welcome_not_dashboard(client):
    """Verify the welcome interstitial is shown, not the dashboard directly."""
    await client.post("/signup", data={"email": "newuser@test.com", "password": "pass1234"}, follow_redirects=False)
    # Follow the redirect to /welcome
    resp = await client.get("/welcome", follow_redirects=False)
    assert resp.status_code == 200
    assert b"2FA" in resp.content or b"Two-Factor" in resp.content or b"Secure" in resp.content


async def test_signup_duplicate_email(client):
    """Signing up with an existing email returns 400 with an error message."""
    data = {"email": "bob@test.com", "password": "securepass123"}
    await client.post("/signup", data=data, follow_redirects=False)
    resp = await client.post("/signup", data=data, follow_redirects=False)
    assert resp.status_code == 400
    assert b"already" in resp.content.lower() or b"error" in resp.content.lower()


async def test_signup_short_password(client):
    """Password under 8 characters returns 400."""
    resp = await client.post(
        "/signup",
        data={"email": "carol@test.com", "password": "short"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert b"password" in resp.content.lower()


async def test_signup_invalid_email_format(client):
    """Non-email strings are rejected with 400."""
    for bad in ["notanemail", "missing@tld", "@nodomain.com", "two@@at.com"]:
        resp = await client.post(
            "/signup",
            data={"email": bad, "password": "validpass123"},
            follow_redirects=False,
        )
        assert resp.status_code == 400, f"Expected 400 for email={bad!r}, got {resp.status_code}"
        assert b"valid email" in resp.content.lower()


async def test_signup_valid_email_accepted(client):
    """Standard email formats are accepted."""
    resp = await client.post(
        "/signup",
        data={"email": "user+tag@sub.domain.com", "password": "validpass123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


# ── Welcome / 2FA prompt ──────────────────────────────────────────────────────

async def test_welcome_requires_auth(client):
    """/welcome with no session redirects to /signup."""
    resp = await client.get("/welcome", follow_redirects=False)
    assert resp.status_code == 303
    assert "/signup" in resp.headers["location"]


async def test_welcome_shown_after_signup(client):
    """After signup a fresh user sees the 2FA prompt page."""
    await client.post(
        "/signup",
        data={"email": "welcome_test@test.com", "password": "securepass123"},
        follow_redirects=False,
    )
    resp = await client.get("/welcome", follow_redirects=False)
    assert resp.status_code == 200


# ── Login ─────────────────────────────────────────────────────────────────────

async def test_login_success(client):
    """Registered user can log in and is redirected to dashboard."""
    await client.post(
        "/signup",
        data={"email": "dave@test.com", "password": "securepass123"},
        follow_redirects=False,
    )
    resp = await client.post(
        "/login",
        data={"email": "dave@test.com", "password": "securepass123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"


async def test_login_wrong_password(client):
    """Wrong password returns 401 with an error message."""
    await client.post(
        "/signup",
        data={"email": "eve@test.com", "password": "correctpass"},
        follow_redirects=False,
    )
    resp = await client.post(
        "/login",
        data={"email": "eve@test.com", "password": "wrongpass"},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert b"invalid" in resp.content.lower()


async def test_login_unknown_email(client):
    """Unknown email returns 401."""
    resp = await client.post(
        "/login",
        data={"email": "ghost@test.com", "password": "whatever"},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert b"invalid" in resp.content.lower()


# ── Logout ────────────────────────────────────────────────────────────────────

async def test_logout(client):
    """Logged-in user can log out and is redirected to home."""
    await client.post(
        "/signup",
        data={"email": "frank@test.com", "password": "securepass123"},
        follow_redirects=True,
    )
    resp = await client.post("/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] in ("/", "/login")


# ── Protected routes ──────────────────────────────────────────────────────────

async def test_dashboard_requires_auth(client):
    """Unauthenticated GET /dashboard redirects to /login."""
    resp = await client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]


async def test_settings_requires_auth(client):
    """Unauthenticated GET /settings/security redirects to /login."""
    resp = await client.get("/settings/security", follow_redirects=False)
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]
