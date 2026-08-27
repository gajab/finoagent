# Derivative Income — MCP server

Exposes FinoAgent's **Derivative Income** desk analysis as a [Model Context Protocol](https://modelcontextprotocol.io)
tool, so an external agent (Gemini CLI, Claude Desktop, …) can scan option-premium income
trades for a ticker and rank them across a large universe.

It calls the **same backend engine** that powers the web UI:

| Layer | Function | Gives you |
|---|---|---|
| Desk (default) | `rank_desk()` | grade + risk-adjusted desk metrics (VaR/CVaR, Omega/Sortino), TA-aware strikes |
| Scan (fast) | `run_derivative_income()` | premium / greeks / vol stats only (no grade) |

Both run against the keyless **yfinance** provider with no user/DB — so it needs **no auth,
no OpenAI key, and no database**. The LLM Quant→Risk→PM debate shown in the UI is a separate,
on-demand step and is intentionally **not** invoked here; the grade and risk-adjusted quality
are fully algorithmic.

The tool logic lives in the backend package (`backend/app/mcp_server/derivative_income.py`)
so it ships inside the Docker image and can be served two ways:

- **HTTP (deployed / recommended)** — mounted *into the web app*, so it runs in the same
  container and on the same port. A remote Gemini CLI connects to a **URL**. ⟵ your setup.
- **stdio (local dev)** — a client on the *same machine* launches `server.py` as a subprocess.

---

## A. HTTP — running in Docker as part of the web server (your setup)

Nothing extra to run. The web app (`backend/app/main.py`) mounts the MCP server at:

```
/mcp/derivative-income/          ← note the trailing slash
```

So on your deployment it's reachable at e.g. `https://your-host/mcp/derivative-income/`
(local: `http://localhost:8000/mcp/derivative-income/`). It shares the container, the port
(8000), and the process with the web app.

### Deploy

1. `mcp>=2.0.0` is now in `backend/requirements.txt`, so a normal image rebuild installs it:
   ```bash
   docker compose build && docker compose up -d
   ```
2. **Set an auth token** (strongly recommended once the port is reachable from the internet).
   Add to the container env (e.g. your `.env`, which docker-compose already loads):
   ```
   MCP_AUTH_TOKEN=some-long-random-secret
   ```
   With it set, every MCP request must send `Authorization: Bearer some-long-random-secret`
   (or `X-API-Key: …`). Leave it unset only for a private/local box.

There is no separate service, port, or process to manage — if the web app is up, so is the
MCP endpoint. Check the startup log for `Mounted MCP server finoagent-derivative-income at
/mcp/derivative-income (auth=token|open)`.

### Connect Gemini CLI (remote → your server)

In `~/.gemini/settings.json` (global) or `.gemini/settings.json` (project), under
`mcpServers`, use **`httpUrl`** (Streamable HTTP):

```json
{
  "mcpServers": {
    "derivative-income": {
      "httpUrl": "https://your-host/mcp/derivative-income/",
      "headers": { "Authorization": "Bearer some-long-random-secret" },
      "timeout": 180000
    }
  }
}
```

- Drop `headers` if you didn't set `MCP_AUTH_TOKEN`.
- Keep the **trailing slash** on the URL (avoids a redirect hop).
- `timeout` is per-call in ms; one ticker in `desk` mode makes ~6–8 yfinance calls (the
  server itself caps each call at 150 s and returns a structured timeout instead of hanging).

Then in Gemini CLI, `/mcp` lists `derivative-income` with its two tools.

### Sanity-check the endpoint

```bash
curl -sS -X POST "https://your-host/mcp/derivative-income/" \
  -H "Authorization: Bearer some-long-random-secret" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

---

## B. stdio — local dev (optional)

If instead a client on the *same machine* should spawn the server:

```json
{
  "mcpServers": {
    "derivative-income": {
      "command": "/Users/rahul/ClaudeCode/stock-research-standalone/backend/venv/bin/python",
      "args": ["/Users/rahul/ClaudeCode/stock-research-standalone/mcp_servers/derivative_income/server.py"],
      "cwd": "/Users/rahul/ClaudeCode/stock-research-standalone/backend",
      "timeout": 180000
    }
  }
}
```

`command` must be the backend venv's Python (it has yfinance / QuantLib / the `app` package).
Same tools, same output — just a different transport. `run.sh` is an equivalent launcher.

---

## Tools

### `analyze_derivative_income`

Scan one ticker and return the full desk analysis, ranked best-first.

| Param | Default | UI equivalent |
|---|---|---|
| `ticker` | — (required) | Ticker |
| `target_dte` | `null` → monthlies ≤45 DTE | Contract Expiry (Auto Monthlies ≤45d) |
| `target_expiration` | `null` | exact expiry `YYYY-MM-DD` (overrides `target_dte`) |
| `min_probability` | `0.90` | Min probability (of NOT being assigned) |
| `min_premium` | `20` | Min premium ($/contract) |
| `structures` | `null` → all | Trade structure |
| `already_own_stock` | `false` | Already own stock |
| `top_n` | `5` | how many ranked trades to return (`0` = all) |
| `mode` | `"desk"` | `"desk"` (full grade) or `"scan"` (fast) |
| `verbose` | `false` | add TA factors / risk-trigger ladder / flags per trade |
| `include_raw` | `false` | attach the complete engine payload under `raw` |

Valid `structures`: `covered_call`, `cash_secured_put`, `short_strangle`,
`credit_spread`, `iron_condor`, `jade_lizard`, `calendar`.

**Returns** (compact, cross-ticker rankable):

```jsonc
{
  "ok": true,
  "mode": "desk",
  "underlying": {
    "ticker", "spot", "as_of", "sofr_pct", "next_earnings", "exercise_style", "week52",
    "beta_1y_spx",
    "atm_iv_pct", "hv30_pct", "hv20_pct", "hv10_pct",
    "forward_rv_har_pct",          // Fwd RV · HAR forecast
    "iv_vs_har_pts", "iv_rank", "iv_percentile", "vol_rank", "vol_percentile",
    "skew_pts", "skew_direction", "term_structure",
    "corporate_actions", "technical_summary", "gex_regime"
  },
  "top_pick": { /* same shape as a trades[] item */ },
  "trades": [
    {
      "structure", "label", "expiration", "dte",
      "algo_grade", "desk_score", "approval_odds", "grade_blocking",   // grading
      "win_pct", "prob_method", "short_delta",                        // win %
      "short_strike", "short_strike_pct_from_spot", "breakeven", "cushion_pct",
      "premium", "premium_per_share", "premium_annualized_pct",
      "static_return_pct", "sofr_excess_pct", "if_assigned_return_pct",
      "capital_at_risk", "capital_basis", "notional_capital", "max_loss", "max_profit",
      "var_95", "cvar_95", "var_99", "cvar_99",                       // capital risk
      "atm_iv_pct", "iv_hv_ratio", "premium_richness", "iv_edge_vp",
      "greeks", "theta_per_day", "vega_exposure",                     // dynamic greeks
      "risk_adjusted": { "quant_score", "quant_verdict", "quant_subscores",
                         "pop_pct", "omega", "sortino", "calmar", "kelly_fraction", ... },
      "quant_analysis": { /* Q-vs-P boundary read + VRP */ },
      "confidence", "liquidity",
      "legs": [ { "action", "type", "strike", "expiration", "bid", "ask", "mid",
                  "iv", "oi", "vol", "delta", "gamma", "theta", "vega" } ]
    }
  ],
  "n_trades_returned": 5,
  "n_trades_total": 14,
  "events": [...], "note": null
}
```

On failure (bad ticker, no chain, timeout): `{ "ok": false, "error": "...", "ticker": "..." }`
— so a loop over a universe keeps going.

### `list_trade_structures`

Returns the valid `structures` values with one-line descriptions. No args.

---

## Bulk workflow (e.g. 300 tickers)

In Gemini CLI:

1. Give it your list of tickers.
2. Ask it to call `analyze_derivative_income` for each (keep `top_n` small — `1`–`3` — to
   keep the aggregate compact; raise `min_premium` / `min_probability` to pre-filter).
3. Ask it to collect the `top_pick` (or `trades`) from every ticker and select, e.g.:
   > "From everything you collected, pick the 50 trades with the highest `desk_score`, then
   > break ties by `risk_adjusted.sortino`, and show grade, win_pct, premium_annualized_pct,
   > cvar_95 and the legs."

Every trade carries the ranking metrics inline (`desk_score`, `algo_grade`, `win_pct`,
`premium_annualized_pct`, `sofr_excess_pct`, `cvar_95`, `risk_adjusted.*`), so the agent
sorts/filters across the whole universe without re-calling the server.

Tips for a large sweep:
- `mode: "scan"` is markedly faster (skips the TA/GEX reads) if you only need premium/greeks/
  vol and not the grade or VaR/CVaR.
- yfinance rate-limits; expect a few seconds per ticker and the occasional `ok: false`
  timeout you can retry.
- Heavy MCP calls share the web app's process, so a 300-ticker sweep competes with live web
  traffic — run big sweeps off-peak, or scale the container.

---

## Where the code lives

- `backend/app/mcp_server/derivative_income.py` — the `MCPServer` + tools (single source of truth).
- `backend/app/mcp_server/http_mount.py` — mounts it into FastAPI over Streamable HTTP + the
  optional bearer-token gate. Called from `backend/app/main.py` (mount + lifespan).
- `mcp_servers/derivative_income/server.py` — thin **stdio** launcher for local dev.
- The engine: `rank_desk` → `run_derivative_income` (scan) + `_finalize_desk` (grade +
  `_opp_desk_metrics`). No LLM, no auth, `user=None, db=None`.
