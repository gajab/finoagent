#!/usr/bin/env python3
"""Derivative Income MCP server — **stdio** launcher (local dev).

The tools live in the backend package (``app.mcp_server.derivative_income``). This thin
launcher runs that same MCP server over **stdio**, for when a client on the SAME machine
launches it as a subprocess (e.g. Claude Desktop, or a local Gemini CLI stdio config).

For the DEPLOYED setup — the server running in Docker as part of the web app, reached by a
remote agent over the network — you do NOT use this script. The web app mounts the same
tools over Streamable HTTP at ``/mcp/derivative-income`` (see
``backend/app/mcp_server/http_mount.py`` and the README). Point Gemini CLI at that URL.

Run:   python server.py            # stdio
Env:   FINOAGENT_BACKEND_DIR       # override path to the FastAPI backend package root
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging → stderr ONLY. stdio MCP requires a clean stdout (JSON-RPC only).
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.environ.get("DERIVINC_MCP_LOGLEVEL", "WARNING"),
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# The yfinance provider best-effort-logs API metrics to a sqlite table that isn't present
# in this standalone context; the failure is caught but noisy — quiet it.
logging.getLogger("app.services.quote_providers.yfinance_provider").setLevel(logging.CRITICAL)

# ---------------------------------------------------------------------------
# Make the FinoAgent backend importable, then pull in the shared MCP server.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND_DIR = Path(os.environ.get("FINOAGENT_BACKEND_DIR", _REPO_ROOT / "backend")).resolve()
if not (_BACKEND_DIR / "app").is_dir():
    raise RuntimeError(
        f"FinoAgent backend package not found at {_BACKEND_DIR} (looked for an 'app/' dir). "
        "Set FINOAGENT_BACKEND_DIR to the backend root."
    )
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.mcp_server.derivative_income import mcp  # noqa: E402


if __name__ == "__main__":
    logging.getLogger("derivative_income_mcp").info(
        "Starting Derivative Income MCP server over stdio (backend=%s)", _BACKEND_DIR)
    mcp.run(transport="stdio")
