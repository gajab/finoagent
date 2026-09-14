"""FastAPI application entry-point for the FinoAgent.ai app."""

import asyncio
import json
import math
import os
import typing
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware as StarletteSessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from . import yf_cache  # noqa: F401 — import-for-side-effect: pins the yfinance tz cache before any service imports/uses yfinance (kills the concurrent "Failed to create TzCache … File exists" race)
from .config import settings
from .database import init_db
from .middleware import SessionMiddleware
from .routers import auth_router, portfolio_router, settings_router, stock_router, proxy_router, agent_router, stock_notes_router, channel_router, tlh_portfolio_router, broker_router, saved_strategy_router, metrics_router, market_router, paper_trade_router
from .routers import tracking_router
from .routers import trade_tracking_router
from .routers import pick_shovel_v2_router
from .routers import debt_entry_router
from .services.scheduler_service import scheduler_loop


# ---------------------------------------------------------------------------
# Lifespan — initialise DB on startup, start scheduler
# ---------------------------------------------------------------------------

# MCP servers mounted over Streamable HTTP (populated after the app is built, below).
# Their streamable-HTTP session managers must be kept running for the app's lifetime.
_mcp_instances: list = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Start the agent scheduler as a background task
    scheduler_task = asyncio.create_task(scheduler_loop())
    try:
        # Run each mounted MCP server's streamable-HTTP session manager for the app's life.
        async with AsyncExitStack() as stack:
            for _m in _mcp_instances:
                await stack.enter_async_context(_m.session_manager.run())
            yield
    finally:
        # Shutdown: cancel the scheduler
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# JSON responses — NaN/Infinity safety net
# ---------------------------------------------------------------------------

def _finite(o):
    """Recursively replace NaN/±Infinity with None.

    Our analytics payloads are assembled from market data (yfinance option chains,
    pandas frames, numpy reductions) where a single missing field arrives as NaN.
    Starlette's JSONResponse serialises with ``allow_nan=False``, so ONE such float
    anywhere in a large payload raised ``ValueError: Out of range float values are
    not JSON compliant`` and turned an otherwise-good 200 into a 500. Emit ``null``
    for those instead — the frontend already treats null as "not available".

    This is the boundary net, not a licence to skip validation: a NaN that reaches
    here is still a bug at its source, it just no longer takes the response with it.
    """
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


class SafeJSONResponse(JSONResponse):
    """JSONResponse that renders non-finite floats as null instead of raising."""

    def render(self, content: typing.Any) -> bytes:
        try:
            return super().render(content)
        except ValueError:
            return json.dumps(
                _finite(content),
                ensure_ascii=False, allow_nan=False, indent=None,
                separators=(",", ":"),
            ).encode("utf-8")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SqubeFi API",
    version="1.0.0",
    lifespan=lifespan,
    default_response_class=SafeJSONResponse,
)


# Log the FULL traceback for any unhandled error (otherwise a 500 is opaque — e.g. the
# "P&L refresh failed: Internal Server Error" on one trade), and echo a short cause to the
# client so it's diagnosable instead of a blank 500. FastAPI's own handlers still deal with
# HTTPException / validation errors (more specific → they win); this only sees the un-caught.
@app.exception_handler(Exception)
async def _log_unhandled(request, exc):  # noqa: ANN001
    import logging
    import traceback
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException
    if isinstance(exc, StarletteHTTPException):   # pass raised HTTP errors through unchanged
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    logging.getLogger("app.unhandled").error(
        "Unhandled error on %s %s\n%s", request.method, request.url.path,
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    )
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})

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
app.include_router(paper_trade_router.router)
app.include_router(metrics_router.router)
app.include_router(market_router.router)
app.include_router(tracking_router.router)
app.include_router(trade_tracking_router.router)
app.include_router(pick_shovel_v2_router.router)
app.include_router(debt_entry_router.router)


# ---------------------------------------------------------------------------
# MCP servers — expose backend analytics as Model-Context-Protocol tools over
# Streamable HTTP at /mcp/<name> (same host/port as the web app), so a remote agent
# (e.g. Gemini CLI) connects to a URL. See app/mcp_server/http_mount.py. Mounted BEFORE
# the SPA catch-all so /mcp/* isn't swallowed by it. Additive: a failure here (e.g. the
# `mcp` package missing) is logged and never blocks web-app startup.
# ---------------------------------------------------------------------------
try:
    from .mcp_server.http_mount import mount_mcp_servers
    _mcp_instances.extend(mount_mcp_servers(app))
except Exception as _mcp_exc:  # noqa: BLE001
    import logging as _logging
    _logging.getLogger(__name__).warning("MCP servers not mounted: %s", _mcp_exc)


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
# Memory diagnostics — watch RSS, confirm the allocator, and hunt leaks in prod.
# Admin-only (same secret as the waitlist endpoints). tracemalloc is OFF by default
# (it itself costs memory + CPU); turn it on only for a hunt, then turn it back off.
# ---------------------------------------------------------------------------

_trace_baseline = None   # tracemalloc snapshot captured at ?trace=start, for ?trace=diff


@app.get("/api/debug/memory")
async def debug_memory(secret: str = "", trace: str = ""):
    """RSS / GC / allocator snapshot; ``?trace=start|diff|stop`` drives a tracemalloc leak hunt.

    Usage:
      GET /api/debug/memory?secret=…               → current rss/peak/gc/allocator
      GET /api/debug/memory?secret=…&trace=start   → begin tracing + capture a baseline
      …exercise the suspect flow a few times…
      GET /api/debug/memory?secret=…&trace=diff    → allocations that GREW since the baseline (the leak)
      GET /api/debug/memory?secret=…&trace=stop    → stop tracing (do this when done — it has overhead)
    """
    global _trace_baseline
    import gc
    import tracemalloc

    expected = (os.environ.get("WAITLIST_ADMIN_SECRET") or settings.SECRET_KEY[:16]).strip()
    if not expected or secret != expected:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Forbidden")

    # Current + peak RSS. /proc is the truth on Linux (Cloud Run); fall back to getrusage elsewhere.
    def _mem_kb() -> tuple[int, int]:
        try:
            cur = peak = 0
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        cur = int(line.split()[1])
                    elif line.startswith("VmHWM:"):
                        peak = int(line.split()[1])
            if cur:
                return cur, (peak or cur)
        except Exception:
            pass
        import resource
        import sys as _sys
        m = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss   # linux=KB, mac=bytes
        kb = m // 1024 if _sys.platform == "darwin" else m
        return kb, kb

    def _allocator() -> str:
        try:
            with open("/proc/self/maps") as f:
                maps = f.read()
            if "libjemalloc" in maps:
                return "jemalloc"
            if "libtcmalloc" in maps:
                return "tcmalloc"
            return "glibc"
        except Exception:
            return "unknown"

    cur_kb, peak_kb = _mem_kb()

    trace = (trace or "").lower()
    trace_msg = None
    if trace == "start":
        tracemalloc.start(25)
        _trace_baseline = tracemalloc.take_snapshot()
        trace_msg = "tracing ON + baseline captured"
    elif trace == "stop":
        _trace_baseline = None
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        trace_msg = "tracing OFF"

    top: list[str] = []
    traced = None
    if tracemalloc.is_tracing():
        cur_t, peak_t = tracemalloc.get_traced_memory()
        traced = {"current_mb": round(cur_t / 1e6, 1), "peak_mb": round(peak_t / 1e6, 1)}
        snap = tracemalloc.take_snapshot()
        if trace == "diff" and _trace_baseline is not None:
            top = [f"+{round(s.size_diff/1e6, 2)}MB ({s.count_diff:+d} objs) {s.traceback[0]}"
                   for s in snap.compare_to(_trace_baseline, "lineno")[:15]]
        else:
            top = [f"{round(s.size/1e6, 2)}MB ({s.count} objs) {s.traceback[0]}"
                   for s in snap.statistics("lineno")[:15]]

    return {
        "rss_mb": round(cur_kb / 1024, 1),
        "peak_rss_mb": round(peak_kb / 1024, 1),
        "allocator": _allocator(),
        "gc_counts": gc.get_count(),
        "gc_objects": len(gc.get_objects()),
        "gc_frozen": (gc.get_freeze_count() if hasattr(gc, "get_freeze_count") else None),
        "tracemalloc": ("on" if tracemalloc.is_tracing() else "off"),
        "traced": traced,
        "top": top,
        "note": trace_msg,
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
    "tracking", "trade-tracking", "my-trades", "calculators", "financial-calculators",
    "roth-ira-conversion", "college-529",
}

class CacheControlledStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

if _frontend_dist.is_dir():
    # Serve static assets (JS, CSS, images) with aggressive caching
    app.mount("/assets", CacheControlledStaticFiles(directory=str(_frontend_dist / "assets")), name="static-assets")

    # Catch-all: static files, prerendered SEO pages, then SPA routing
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        base = _frontend_dist.resolve()
        file_path = (base / full_path).resolve()
        
        no_cache_headers = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
        
        if not file_path.is_relative_to(base):
            return FileResponse(str(base / "index.html"), status_code=404, headers=no_cache_headers)
        
        if file_path.is_file():
            headers = no_cache_headers if file_path.suffix == ".html" else {}
            return FileResponse(str(file_path), headers=headers)
            
        # Extension-less URLs for prerendered SEO pages, e.g. /agentic-trading
        html_path = base / f"{full_path.strip('/')}.html"
        if full_path and html_path.is_file():
            return FileResponse(str(html_path), media_type="text/html", headers=no_cache_headers)
            
        status = 200 if full_path.split("/", 1)[0] in _SPA_ROUTES else 404
        return FileResponse(str(base / "index.html"), status_code=status, headers=no_cache_headers)
