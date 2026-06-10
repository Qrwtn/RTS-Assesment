import re

import pyotp
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.limiter import limiter
from app.middleware.csrf import csrf_protect
from app.modules.auth.service import hash_password, verify_password
from app.modules.user import repository as user_repo
from app.templates import templates

router = APIRouter()

# RFC-5322-lite: something@something.tld
_EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]{2,}$')


@router.get("/signup")
async def signup_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(request, "auth/signup.html", {})


@router.post("/signup")
@limiter.limit("5/minute")
async def signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
):
    email = email.lower().strip()

    if not _EMAIL_RE.match(email):
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {"error": "Please enter a valid email address.", "email": email},
            status_code=400,
        )

    if await user_repo.get_by_email(db, email):
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {"error": "An account with that email already exists.", "email": email},
            status_code=400,
        )
    if len(password) < 8:
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {"error": "Password must be at least 8 characters.", "email": email},
            status_code=400,
        )
    if len(password) > 72:
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {"error": "Password must be 72 characters or fewer.", "email": email},
            status_code=400,
        )

    user = await user_repo.create_user(db, email, hash_password(password))
    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/welcome", status_code=303)


@router.get("/welcome")
async def welcome_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Post-signup interstitial — prompt user to set up 2FA."""
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/signup", status_code=303)
    user = await user_repo.get_by_id(db, user_id)
    if not user:
        return RedirectResponse("/signup", status_code=303)
    if user.two_factor_enabled:
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(request, "auth/welcome.html", {"user": user})


@router.get("/login")
async def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(request, "auth/login.html", {})


@router.post("/login")
@limiter.limit("5/minute")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
):
    email = email.lower().strip()
    user = await user_repo.get_by_email(db, email)

    if not user or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"error": "Invalid email or password."},
            status_code=401,
        )

    if user.two_factor_enabled:
        request.session.clear()
        request.session["pending_2fa_user_id"] = user.id
        return RedirectResponse("/login/2fa", status_code=303)

    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/login/2fa")
async def verify_2fa_page(request: Request):
    if not request.session.get("pending_2fa_user_id"):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "auth/verify_2fa.html", {})


@router.post("/login/2fa")
@limiter.limit("5/minute")
async def verify_2fa(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
):
    user_id = request.session.get("pending_2fa_user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    user = await user_repo.get_by_id(db, user_id)
    if not user or not user.totp_secret:
        return RedirectResponse("/login", status_code=303)

    if not pyotp.TOTP(user.totp_secret).verify(code.strip()):
        return templates.TemplateResponse(
            request,
            "auth/verify_2fa.html",
            {"error": "Invalid code. Please try again."},
            status_code=401,
        )

    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/logout")
async def logout(request: Request, _csrf: None = Depends(csrf_protect)):
    request.session.clear()
    return RedirectResponse("/", status_code=303)
