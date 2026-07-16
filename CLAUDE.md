# FinoAgent — Claude Code Instructions

## Knowledge graph (graphify)

`graphify-out/graph.json` exists. **Before reading any file to implement a feature,
query the graph first** using the shell commands below. This is mandatory — do not
skip straight to reading files.

### How to use it

```bash
# Understand what already handles a domain
graphify query "<what you're building>"

# Find which files a symbol touches
graphify query "<function or class name>"

# Trace the path between two things
graphify path "<source concept>" "<target concept>"
```

### Required pre-coding workflow

When asked to build ANY new feature, always run these steps in order:

1. **Query the graph** with 2-3 natural-language questions about the domain
   (e.g. "how does portfolio data flow", "what handles auth", "where is yfinance called")
2. **Read only the files the graph points to** — not the whole codebase
3. **Then code**

If you are unsure what to query, translate the feature request into the
backend concern + frontend concern, e.g.:
- Feature: "add sector heatmap" → query: "market overview service sector data"
  and "MarketPage frontend sector components"
- Feature: "add email alerts" → query: "email service scheduler" and
  "settings page user preferences"

### Stack

- Backend: FastAPI + SQLAlchemy async + yfinance + OpenAI
- Frontend: React + TypeScript + Tailwind + Chart.js
- Auth: Google OAuth, sessions in DB, Fernet-encrypted API keys
- Caching: `cache_service.get_cached / set_cached` with TTL in seconds
  (`_TTL_PRICE = 900`, `_TTL_STATIC = 86400`)

### Key god-nodes (always relevant)

- `apiFetch()` — every frontend API call goes through this (`frontend/src/api.ts:5`)
- `call_llm()` — every LLM call goes through this (`backend/app/services/llm_service.py`)
- `get_user_api_key()` — how services get the user's OpenAI key
- `MarketDataProvider` — abstract base for all market data (`backend/app/providers/base.py`)

### Adding a new backend endpoint

Follow the exact pattern in `market_router.py`:
1. Import the service function
2. Add `@router.get/post("/your-path")`
3. Inject `user: User = Depends(get_current_user)` and `db: AsyncSession = Depends(get_db)`
4. Check cache with `get_cached`, compute, store with `set_cached`

### Adding a new frontend component

1. New component file in `frontend/src/components/`
2. Add type to `frontend/src/types.ts`
3. Add API function to `frontend/src/api.ts` using `apiFetch<YourType>('/api/...')`
4. Import and place in the appropriate page in `frontend/src/pages/`
