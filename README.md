# 📊 FinoAgent.ai

A full-stack AI-powered stock research platform with Google OAuth authentication, per-user API key management, and comprehensive stock analysis tools.

## Features

- **🔐 Google OAuth Login** — Secure authentication via Google accounts
- **🔑 Per-User API Keys** — Each user stores their own API keys (encrypted at rest)
- **⏰ Session Security** — Sessions expire after 1 hour of inactivity
- **📰 News & AI Summary** — Recent news with AI-powered sentiment analysis
- **📊 Analyst Ratings** — Visual breakdown of buy/sell/hold recommendations
- **📈 Financial Charts** — 3-year EPS, Revenue, and Free Cash Flow
- **💰 Earnings Summary** — Latest quarterly results with earnings call insights
- **🔧 Technical Analysis** — 15-day intraday chart with RSI, support/resistance, volume analysis
- **📉 Options & Volatility** — IV, HV, put/call ratio, options chain analysis
- **🎯 Trading Opportunities** — Cash Secured Puts & Covered Calls (≥4% annualized, ≥88% probability OTM)
- **🌍 Industry Watch** — Sector trends and economic factors

## Architecture

```
┌─────────────────────────────────────┐
│           React Frontend            │
│  (Vite + Tailwind + DaisyUI)        │
│  - Google Login flow                │
│  - Dashboard with charts            │
│  - Settings for API keys            │
└───────────────┬─────────────────────┘
                │ HTTP (cookies)
┌───────────────┴─────────────────────┐
│          FastAPI Backend             │
│  - Google OAuth2 (authlib)           │
│  - Session middleware (1hr timeout)  │
│  - Encrypted API key storage         │
│  - Stock data (yfinance)             │
│  - LLM proxy (OpenAI)               │
│  - Search proxy (SerpAPI)            │
└───────────────┬─────────────────────┘
                │
        ┌───────┴───────┐
        │   SQLite DB   │
        │  users, keys, │
        │   sessions    │
        └───────────────┘
```

## Quick Start

### Prerequisites

- Python 3.12+
- Node.js 18+
- A Google Cloud project with OAuth 2.0 credentials

### 1. Set Up Google OAuth

1. Go to [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials)
2. Create an **OAuth 2.0 Client ID** (Web application)
3. Add authorized redirect URI: `http://localhost:8000/api/auth/callback`
4. Copy the Client ID and Client Secret

### 2. Configure Environment

```bash
cd stock-research-standalone
cp .env.example .env
```

Edit `.env` and fill in your values:

```bash
# Generate SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Generate ENCRYPTION_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 3. Run with Docker (Recommended)

```bash
docker compose up --build
```

The app will be available at **http://localhost:8000**

### 4. Run for Development (Without Docker)

**Backend:**
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**Frontend (separate terminal):**
```bash
cd frontend
npm install
npm run dev
```

Frontend runs at **http://localhost:5173** (proxies API to :8000 via Vite)

## User API Keys

After logging in, users go to **Settings** to configure:

| Key | Service | Purpose | Get it at |
|-----|---------|---------|-----------|
| OpenAI API Key | OpenAI | AI news summaries, earnings analysis | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| SerpAPI Key | SerpAPI | Web search for industry updates | [serpapi.com/manage-api-key](https://serpapi.com/manage-api-key) |

API keys are **encrypted at rest** using Fernet symmetric encryption. Each user's keys are stored separately and only decrypted when making API calls on their behalf.

> 💡 The app works without API keys — stock data, charts, analyst ratings, options analysis all work. AI summaries and web search features require the respective keys.

## Deployment

### Deploy to Railway / Render / Fly.io

1. Push the repo to GitHub
2. Connect to your hosting platform
3. Set all environment variables from `.env.example`
4. Update `FRONTEND_URL` to your production URL
5. Update Google OAuth redirect URI to `https://yourdomain.com/api/auth/callback`

### Deploy to a VPS

```bash
# Build and run
docker compose up -d --build

# Or use a reverse proxy (nginx) in front for HTTPS
```

### Environment Variables for Production

```bash
DATABASE_URL=sqlite+aiosqlite:///./data/stock_research.db
GOOGLE_CLIENT_ID=your-production-client-id
GOOGLE_CLIENT_SECRET=your-production-client-secret
SECRET_KEY=generate-a-strong-random-key
ENCRYPTION_KEY=generate-a-fernet-key
FRONTEND_URL=https://yourdomain.com
SESSION_TIMEOUT_MINUTES=60
```

## Project Structure

```
stock-research-standalone/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app + static file serving
│   │   ├── config.py            # Settings from environment
│   │   ├── database.py          # SQLAlchemy async setup
│   │   ├── models.py            # User, ApiKey, Session models
│   │   ├── auth.py              # Google OAuth + session + encryption
│   │   ├── middleware.py        # Session validation middleware
│   │   ├── routers/
│   │   │   ├── auth_router.py   # Login, callback, logout, me
│   │   │   ├── stock_router.py  # Stock data endpoint
│   │   │   ├── settings_router.py # API key CRUD
│   │   │   └── proxy_router.py  # LLM & search proxy
│   │   └── services/
│   │       ├── stock_service.py # yfinance data fetching
│   │       ├── llm_service.py   # OpenAI API proxy
│   │       └── search_service.py # SerpAPI proxy
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.tsx              # Routes + auth provider
│   │   ├── api.ts               # API client
│   │   ├── types.ts             # TypeScript types
│   │   ├── contexts/
│   │   │   └── AuthContext.tsx   # Auth state management
│   │   ├── pages/
│   │   │   ├── LoginPage.tsx    # Google sign-in
│   │   │   ├── DashboardPage.tsx # Main stock dashboard
│   │   │   └── SettingsPage.tsx # API key management
│   │   └── components/          # All stock analysis components
│   ├── package.json
│   └── vite.config.ts
├── Dockerfile                   # Multi-stage build
├── docker-compose.yml
├── .env.example
└── README.md
```

## Security

- **Authentication**: Google OAuth 2.0 (no passwords stored)
- **Sessions**: Server-side with signed cookies, 1-hour inactivity timeout
- **API Keys**: Fernet-encrypted at rest, never exposed in API responses (masked only)
- **CORS**: Restricted to configured frontend origin
- **Cookies**: httpOnly, SameSite=Lax

## License

MIT
