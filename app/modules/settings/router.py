import base64
from io import BytesIO

import pyotp
import qrcode
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.csrf import csrf_protect
from app.middleware.auth_guard import get_current_user
from app.modules.user import repository as user_repo
from app.modules.user.models import User
from app.templates import templates

router = APIRouter(prefix="/settings")


@router.get("/security")
async def security_page(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    return templates.TemplateResponse(
        request,
        "settings/security.html",
        {"user": current_user},
    )


@router.post("/2fa/setup")
async def setup_2fa(
    request: Request,
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(csrf_protect),
):
    """Generate a TOTP secret and display the QR code for scanning."""
    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret).provisioning_uri(
        name=current_user.email, issuer_name="StockApp"
    )

    buf = BytesIO()
    qrcode.make(uri).save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()

    request.session["pending_totp_secret"] = secret

    return templates.TemplateResponse(
        request,
        "settings/security.html",
        {
            "user": current_user,
            "qr_code_b64": qr_b64,
            "totp_secret": secret,
        },
    )


@router.post("/2fa/enable")
async def enable_2fa(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(csrf_protect),
):
    """Verify the user scanned the QR code."""
    secret = request.session.get("pending_totp_secret")
    if not secret:
        return RedirectResponse("/settings/security", status_code=303)

    if not pyotp.TOTP(secret).verify(code.strip()):
        return templates.TemplateResponse(
            request,
            "settings/security.html",
            {
                "user": current_user,
                "error": "Invalid code. Please scan the QR code again and retry.",
            },
            status_code=400,
        )

    await user_repo.update_totp(db, current_user.id, secret, enabled=True)
    request.session.pop("pending_totp_secret", None)

    return templates.TemplateResponse(
        request,
        "settings/security.html",
        {
            "user": current_user,
            "success": "Two-factor authentication enabled successfully.",
        },
    )


@router.post("/2fa/disable")
async def disable_2fa(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(csrf_protect),
):
    """Require a valid TOTP code before disabling."""
    if not current_user.two_factor_enabled or not current_user.totp_secret:
        return RedirectResponse("/settings/security", status_code=303)

    if not pyotp.TOTP(current_user.totp_secret).verify(code.strip()):
        return templates.TemplateResponse(
            request,
            "settings/security.html",
            {
                "user": current_user,
                "error": "Invalid code. 2FA has not been disabled.",
            },
            status_code=400,
        )

    await user_repo.update_totp(db, current_user.id, None, enabled=False)
    return templates.TemplateResponse(
        request,
        "settings/security.html",
        {
            "user": current_user,
            "success": "Two-factor authentication has been disabled.",
        },
    )
