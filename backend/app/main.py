"""FastAPI application entry-point for the FinoAgent.ai app."""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware as StarletteSessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from .config import settings
from .database import init_db
from .middleware import SessionMiddleware
from .routers import auth_router, portfolio_router, settings_router, stock_router, proxy_router, agent_router, stock_notes_router, channel_router, tlh_portfolio_router, broker_router, saved_strategy_router, metrics_router, market_router
from .routers import tracking_router
from .routers import pick_shovel_v2_router
from .routers import debt_entry_router
from .services.scheduler_service import scheduler_loop


# ---------------------------------------------------------------------------
# Lifespan — initialise DB on startup, start scheduler
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Start the agent scheduler as a background task
    scheduler_task = asyncio.create_task(scheduler_loop())
    yield
    # Shutdown: cancel the scheduler
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SqubeFi API",
    version="1.0.0",
    lifespan=lifespan,
)

# Proxy headers middleware — trust X-Forwarded-Proto / X-Forwarded-For from
# reverse proxies like ngrok so request.url_for() generates correct URLs.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# CORS — allow the frontend origin + any configured ALLOWED_ORIGINS with credentials
_cors_origins: list[str] = [settings.FRONTEND_URL]
if settings.ALLOWED_ORIGINS:
    _cors_origins.extend(
        o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=r"https://.*\.ngrok-free\.app",  # auto-allow ngrok URLs
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Starlette session middleware (provides request.session for authlib OAuth)
app.add_middleware(StarletteSessionMiddleware, secret_key=settings.SECRET_KEY)

# Custom session middleware (reads cookie, attaches user to request.state)
app.add_middleware(SessionMiddleware)

# Routers
app.include_router(auth_router.router)
app.include_router(stock_router.router)
app.include_router(settings_router.router)
app.include_router(proxy_router.router)
app.include_router(portfolio_router.router)
app.include_router(agent_router.router)
app.include_router(stock_notes_router.router)
app.include_router(channel_router.router)
app.include_router(tlh_portfolio_router.router)
app.include_router(broker_router.router)
app.include_router(saved_strategy_router.router)
app.include_router(metrics_router.router)
app.include_router(market_router.router)
app.include_router(tracking_router.router)
app.include_router(pick_shovel_v2_router.router)
app.include_router(debt_entry_router.router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Waitlist
# ---------------------------------------------------------------------------

from pydantic import BaseModel
from .services.email_service import send_waitlist_email

class WaitlistRequest(BaseModel):
    email: str

@app.post("/api/waitlist")
async def waitlist_signup(req: WaitlistRequest):
    """Save waitlist signup to DB (always succeeds), then fire email as best-effort."""
    import logging
    from sqlalchemy.exc import IntegrityError
    from .database import async_session
    from .models import WaitlistEntry

    _log = logging.getLogger(__name__)
    email = req.email.strip().lower()

    # 1. Persist to DB — this is the source of truth.
    already_exists = False
    try:
        async with async_session() as session:
            entry = WaitlistEntry(email=email)
            session.add(entry)
            await session.commit()
            _log.info(f"Waitlist signup saved: {email}")
    except IntegrityError:
        # Duplicate signup — still a success from the user's perspective.
        already_exists = True
        _log.info(f"Waitlist duplicate (already signed up): {email}")
    except Exception as exc:
        _log.exception(f"Waitlist DB save failed for {email}: {exc}")
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail="Failed to save waitlist signup")

    # 2. Best-effort email notification — never blocks the response.
    if not already_exists:
        try:
            await send_waitlist_email(email)
        except Exception as exc:
            _log.warning(f"Waitlist email notification failed (signup still recorded): {exc}")

    return {"status": "ok", "message": "Successfully joined the waitlist"}


@app.get("/api/waitlist/entries")
async def get_waitlist_entries(secret: str = ""):
    """Return all waitlist signups. Protected by a simple query-param secret.
    Usage: GET /api/waitlist/entries?secret=<WAITLIST_ADMIN_SECRET>
    Set WAITLIST_ADMIN_SECRET env var to enable (defaults to SECRET_KEY[:16]).
    """
    import logging
    from sqlalchemy import select
    from .database import async_session
    from .models import WaitlistEntry

    _log = logging.getLogger(__name__)

    # Simple auth: must supply the first 16 chars of SECRET_KEY (or a dedicated env var)
    expected = (os.environ.get("WAITLIST_ADMIN_SECRET") or settings.SECRET_KEY[:16]).strip()
    if not expected or secret != expected:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Forbidden")

    async with async_session() as session:
        result = await session.execute(
            select(WaitlistEntry).order_by(WaitlistEntry.signed_up_at.desc())
        )
        entries = result.scalars().all()

    return {
        "count": len(entries),
        "entries": [
            {"id": e.id, "email": e.email, "signed_up_at": e.signed_up_at.isoformat(), "notified": e.notified}
            for e in entries
        ],
    }


@app.get("/api/waitlist/smtp-check")
async def smtp_check(secret: str = ""):
    """Diagnose SMTP configuration without actually sending. Admin-only."""
    expected = (os.environ.get("WAITLIST_ADMIN_SECRET") or settings.SECRET_KEY[:16]).strip()
    if not expected or secret != expected:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Forbidden")

    return {
        "SMTP_HOST": settings.SMTP_HOST,
        "SMTP_PORT": settings.SMTP_PORT,
        "SMTP_USER": settings.SMTP_USER,
        "SMTP_PASS_set": bool(settings.SMTP_PASS),
        "SMTP_PASS_len": len(settings.SMTP_PASS) if settings.SMTP_PASS else 0,
        "SMTP_USE_TLS": settings.SMTP_USE_TLS,
        "GCP_PROJECT_ID": bool(settings.GCP_PROJECT_ID),
    }


# ---------------------------------------------------------------------------
# Serve frontend static files in production (Docker)
# ---------------------------------------------------------------------------

from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

_frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

# Client-side routes rendered by the SPA. Unknown paths get index.html with a
# 404 status so crawlers don't index junk URLs as duplicates of the homepage.
_SPA_ROUTES = {
    "", "login", "market", "dashboard", "portfolio", "agents", "strategies",
    "debt", "settings", "ai-research", "impact", "channels", "metrics",
    "tracking", "my-trades", "calculators", "financial-calculators",
    "roth-ira-conversion", "college-529",
}

if _frontend_dist.is_dir():
    # Serve static assets (JS, CSS, images)
    app.mount("/assets", StaticFiles(directory=str(_frontend_dist / "assets")), name="static-assets")

    # Catch-all: static files, prerendered SEO pages, then SPA routing
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        base = _frontend_dist.resolve()
        file_path = (base / full_path).resolve()
        if not file_path.is_relative_to(base):
            return FileResponse(str(base / "index.html"), status_code=404)
        if file_path.is_file():
            return FileResponse(str(file_path))
        # Extension-less URLs for prerendered SEO pages, e.g. /agentic-trading
        html_path = base / f"{full_path.strip('/')}.html"
        if full_path and html_path.is_file():
            return FileResponse(str(html_path), media_type="text/html")
        status = 200 if full_path.split("/", 1)[0] in _SPA_ROUTES else 404
        return FileResponse(str(base / "index.html"), status_code=status)
