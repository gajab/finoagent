"""Authentication routes: Google OAuth login, callback, me, logout."""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    COOKIE_NAME,
    create_session,
    get_current_user,
    oauth,
    sign_session_id,
)
from ..config import settings
from ..database import get_db
from ..models import Session, User, AllowedUser

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _get_base_url(request: Request) -> str:
    """Derive the external base URL from the incoming request.

    Works behind proxies (ngrok, Cloudflare, etc.) by respecting
    X-Forwarded-Proto / X-Forwarded-Host headers (handled by
    ProxyHeadersMiddleware).  Falls back to ``settings.FRONTEND_URL``.
    """
    # With ProxyHeadersMiddleware the request.base_url already reflects
    # forwarded proto + host.  Use that if it looks external.
    base = str(request.base_url).rstrip("/")
    if "localhost" not in base and "127.0.0.1" not in base:
        return base
    # Explicit forwarded headers (ngrok always sends these)
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")
    if forwarded_host:
        proto = forwarded_proto or "https"
        return f"{proto}://{forwarded_host}"
    return settings.FRONTEND_URL


# --------------------------------------------------------------------------
# GET /api/auth/google — redirect to Google consent screen
# --------------------------------------------------------------------------
@router.get("/google")
async def login_google(request: Request):
    base_url = _get_base_url(request)
    redirect_uri = f"{base_url}/api/auth/callback"
    # Save the origin so we can redirect the user back after OAuth completes
    request.session["login_origin"] = base_url
    log.info("OAuth redirect_uri=%s  login_origin=%s", redirect_uri, base_url)
    return await oauth.google.authorize_redirect(request, redirect_uri)


# --------------------------------------------------------------------------
# GET /api/auth/callback — handle Google OAuth callback
# --------------------------------------------------------------------------
@router.get("/callback", name="auth_callback")
async def auth_callback(request: Request, db: AsyncSession = Depends(get_db)):
    token = await oauth.google.authorize_access_token(request)
    userinfo = token.get("userinfo")
    if userinfo is None:
        # Fallback: fetch from userinfo endpoint
        resp = await oauth.google.get("https://openidconnect.googleapis.com/v1/userinfo", token=token)
        userinfo = resp.json()

    google_id = userinfo["sub"]
    email = userinfo.get("email", "").lower()
    name = userinfo.get("name", "")
    picture = userinfo.get("picture")
    
    frontend_url = request.session.pop("login_origin", None) or _get_base_url(request)

    # ----------------------------------------------------------------------
    # Allowlist Enforcement
    # ----------------------------------------------------------------------
    if email != "karwa.rahul@gmail.com":
        allow_res = await db.execute(select(AllowedUser).where(AllowedUser.email == email))
        allowed_user = allow_res.scalar_one_or_none()
        if not allowed_user:
            log.warning("Unauthorized login attempt blocked for email: %s", email)
            # Redirect back with error flag instead of creating session/user
            redirect_url = f"{frontend_url}?error=unauthorized"
            return RedirectResponse(url=redirect_url, status_code=302)

    # Upsert user
    result = await db.execute(select(User).where(User.google_id == google_id))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(google_id=google_id, email=email, name=name, picture=picture)
        db.add(user)
        await db.commit()
        await db.refresh(user)
    else:
        user.email = email
        user.name = name
        user.picture = picture
        await db.commit()

    # Create session
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    raw_session_id = await create_session(db, user.id, ip_address=ip, user_agent=ua)

    # Redirect to the origin saved during login initiation
    is_https = frontend_url.startswith("https")
    log.info("Post-login redirect -> %s  (secure=%s)", frontend_url, is_https)

    response = RedirectResponse(url=frontend_url, status_code=302)
    signed = sign_session_id(raw_session_id)
    response.set_cookie(
        key=COOKIE_NAME,
        value=signed,
        httponly=True,
        samesite="none" if is_https else "lax",
        secure=is_https,
        path="/",
        max_age=settings.SESSION_TIMEOUT_MINUTES * 60,
    )
    return response


# --------------------------------------------------------------------------
# GET /api/auth/me — return current user info
# --------------------------------------------------------------------------
ADMIN_EMAIL = "karwa.rahul@gmail.com"

@router.get("/me")
async def me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Admin is always premium; other users inherit is_premium from AllowedUser row
    is_premium = user.email == ADMIN_EMAIL
    if not is_premium:
        result = await db.execute(select(AllowedUser).where(AllowedUser.email == user.email))
        allowed = result.scalar_one_or_none()
        is_premium = bool(allowed and allowed.is_premium)

    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
        "is_premium": is_premium,
    }


# --------------------------------------------------------------------------
# POST /api/auth/logout — delete session, clear cookie
# --------------------------------------------------------------------------
@router.post("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        from ..auth import unsign_session_id

        raw_session_id = unsign_session_id(token)
        if raw_session_id:
            result = await db.execute(select(Session).where(Session.session_id == raw_session_id))
            session = result.scalar_one_or_none()
            if session:
                await db.delete(session)
                await db.commit()

    response_data = {"ok": True}
    from fastapi.responses import JSONResponse

    response = JSONResponse(content=response_data)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response
