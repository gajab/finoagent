"""MCP servers that expose FinoAgent backend services as Model-Context-Protocol tools.

Each module defines an ``MCPServer`` instance plus its tools. They can be served two ways:

* mounted into the FastAPI web app over Streamable HTTP (production / Docker) — see
  ``http_mount.mount_mcp_servers`` and its use in ``app.main``;
* run standalone over stdio for local dev — see ``mcp_servers/derivative_income/server.py``.

The engines they wrap (``rank_desk`` / ``run_derivative_income``) run with ``user=None,
db=None`` against the keyless yfinance provider, so these tools need no auth, no OpenAI
key and no database.
"""
