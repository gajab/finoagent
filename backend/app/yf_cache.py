"""One-time yfinance timezone-cache configuration (import for side effect).

The symptom this fixes (seen repeatedly in the container logs)::

    Failed to create TzCache, reason: Error creating TzCache folder:
    '/root/.cache/py-yfinance' reason: [Errno 17] File exists.
    TzCache will not be used.

yfinance lazily creates a per-machine timezone cache the first time any ticker
is priced. Its folder creation is a non-atomic ``os.path.isdir()`` check followed
by ``os.makedirs()`` — so when several requests price tickers concurrently (which
is the normal case for this app: many services fan out yfinance calls), two
threads both see "no folder", both call ``makedirs``, and the loser raises
``EEXIST``. yfinance swallows that into the warning above and then runs WITHOUT
the tz cache — noisy logs plus a needless timezone lookup on every call.

The fix is to win the race before it can happen: point the cache at a directory
we control and **pre-create it once at startup** (``exist_ok=True`` is atomic-safe),
before any request thread runs. Once the folder exists, yfinance's ``isdir`` check
passes on every concurrent call and it never attempts the racy ``makedirs``.

We also move the cache off ``/root/.cache`` (only writable because the container
happens to run as root) onto the app's own writable data dir, so it keeps working
regardless of the process user.

Importing this module runs the configuration. It is imported first thing in
``main.py`` so the location is set before the app serves traffic. Never raises —
a cache we couldn't configure just falls back to yfinance's default behaviour.
"""

from __future__ import annotations

import logging
import os
import tempfile

logger = logging.getLogger(__name__)


def _cache_dir() -> str:
    """A writable directory for the yfinance tz cache, stable across restarts.

    Preference order: an explicit override, the app's own data dir (present and
    writable in the container — see the Dockerfile's ``mkdir -p /app/data``), then
    the OS temp dir as a universal fallback for local dev.
    """
    override = os.environ.get("YF_TZ_CACHE_DIR")
    if override:
        return override
    if os.path.isdir("/app/data"):
        return "/app/data/py-yfinance"
    return os.path.join(tempfile.gettempdir(), "py-yfinance")


def configure() -> None:
    try:
        cache_dir = _cache_dir()
        # Pre-create it ONCE, race-free, so no request thread ever hits the
        # check-then-makedirs window that yfinance loses under concurrency. All three
        # yfinance caches (Tz, Cookie, ISIN) share this single "py-yfinance" folder, so
        # creating it here defuses the race for every one of them.
        os.makedirs(cache_dir, exist_ok=True)
        import yfinance as yf

        # set_cache_location redirects all three caches; set_tz_cache_location is its alias
        # in current yfinance but historically only moved the tz cache — prefer the former.
        setter = getattr(yf, "set_cache_location", None) or yf.set_tz_cache_location
        setter(cache_dir)
        logger.info("yfinance caches pinned to %s", cache_dir)
    except Exception as exc:  # noqa: BLE001 — configuration must never break startup
        logger.warning("Could not configure yfinance cache: %s", exc)


configure()
