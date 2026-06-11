# RTS Labs Code Assessment

[![CI](https://github.com/Qrwtn/RTS-Assesment/actions/workflows/ci.yml/badge.svg)](https://github.com/Qrwtn/RTS-Assesment/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Qrwtn/RTS-Assesment/actions/workflows/codeql.yml/badge.svg)](https://github.com/Qrwtn/RTS-Assesment/actions/workflows/codeql.yml)
![Python](https://img.shields.io/badge/python-3.12-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)
![Deployed on Railway](https://img.shields.io/badge/deployed-Railway-8B5CF6?logo=railway&logoColor=white)

A full-stack stock market intelligence platform built for the Senior Software Engineer assessment for **RTS Labs**. Users can look up real-time quotes, track a personal watchlist, view price history charts, and receive AI-generated analysis powered by Claude and sourced from Reddit. It also includes an optional 2FA as security.

---

## Features

- **Real-time quotes**: live price, OHLC breakdown, % change, and peer tickers via Finnhub
- **Interactive price chart**: 1D / 1W / 1M / 6M / 1Y price history from Yahoo Finance with animated OHLC values that update per timeframe
- **AI stock analysis**: per-symbol Analysis tab combining analyst price targets (Yahoo Finance), recent news headlines (Finnhub), Reddit mention trends (ApeWisdom), and a Claude Haiku narrative summary; cached 1 hour
- **AI market buzz**: global Reddit sentiment widget summarised by Claude, covering r/wallstreetbets and r/stocks; rendered as markdown
- **Personal watchlist**: star any stock to track it; sparklines on each row; live prices on page load
- **Paper portfolio**: automatic realised/unrealised P&L tracking from the moment a stock is starred, benchmarked against SPY
- **Secure authentication**: bcrypt password hashing, session fixation prevention, CSRF double-submit tokens on all forms, rate-limited login and signup
- **TOTP 2FA**: optional time-based one-time passwords with QR code enrollment
- **Live market ticker**: animated top-bar ticker pulling from a cached batch of symbols
- **Keyboard shortcut**: press `/` anywhere to jump to the search bar

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.12 · FastAPI 0.115 · Uvicorn (ASGI) |
| **Database** | PostgreSQL 16 · Async SQLAlchemy 2.0 · asyncpg |
| **Cache** | Redis 7 · cache-aside pattern with TTL keying |
| **Auth** | bcrypt · Starlette SessionMiddleware · pyotp (TOTP) |
| **AI** | Anthropic Claude Haiku (`claude-haiku-4-5-20251001`) |
| **External APIs** | Finnhub (quote, news, peers) · Yahoo Finance (candles, analyst targets) · ApeWisdom (Reddit mentions) |
| **Frontend** | Jinja2 templates · Tailwind CSS (CDN) · HTMX 2.0 · Chart.js 4.4 · Lucide icons · marked.js |
| **Rate Limiting** | slowapi (limits library, per-IP) |
| **Testing** | pytest · pytest-asyncio · httpx AsyncClient · real Postgres (no mocks) |
| **CI/CD** | GitHub Actions · CodeQL · Dependabot |
| **Deployment** | Docker (multi-stage) · Railway |

---

## Architecture

```
Browser
  │
  ▼
FastAPI (Uvicorn ASGI)
  │
  ├── SessionMiddleware       ← signed cookie, 24hr TTL
  ├── CSRFTokenMiddleware     ← double-submit token on all POSTs
  ├── SecurityHeadersMiddleware ← CSP, X-Frame-Options, nosniff, etc.
  │
  ├── /auth/*                ← signup, login, logout, 2FA verify
  ├── /stock/lookup          ← server-rendered quote page (POST)
  ├── /api/stock/*           ← JSON: chart, analysis, search (rate-limited)
  ├── /api/reddit/summary    ← JSON: Reddit sentiment widget (rate-limited)
  ├── /api/portfolio         ← JSON: paper portfolio P&L
  ├── /settings/*            ← 2FA enrol / disable
  └── /health                ← Railway health check (pings DB + Redis)
        │
        ├── PostgreSQL        ← users, favorites, searches, portfolio history
        ├── Redis             ← quote cache (5 min) · analysis cache (1 hr) · KPI cache (5 min)
        │
        ├── Finnhub API       ← real-time quote, company news, peer tickers
        ├── Yahoo Finance     ← OHLC candles, analyst price targets
        ├── ApeWisdom API     ← Reddit mention counts + trends
        └── Anthropic API     ← Claude Haiku (AI narrative, market buzz summary)
```

Concurrent external API calls use `asyncio.gather`. Chart data, AI analysis, and Reddit sentiment all fetch in parallel rather than sequentially.

---

## Quickstart

### Prerequisites

- Python 3.12+
- Docker (for Postgres + Redis)
- API keys: [Finnhub](https://finnhub.io) (free tier) · [Anthropic](https://console.anthropic.com) (pay-per-use)

### 1. Clone and install

```bash
git clone https://github.com/Qrwtn/RTS-Assesment.git
cd RTS-Assesment
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — fill in your API keys and generate a SECRET_KEY:
python -c "import secrets; print(secrets.token_hex(32))"
```

### 3. Start services and run

```bash
make docker-up   # starts Postgres + Redis via Docker Compose
make dev         # starts FastAPI with hot reload on :8000
```

Open [http://localhost:8000](http://localhost:8000).

---

## Make Commands

| Command | Description |
|---|---|
| `make install` | Install Python dependencies |
| `make dev` | Run dev server with hot reload on :8000 |
| `make test` | Run full test suite (spins up a throwaway Postgres container) |
| `make test-db-up` | Start the test-only Postgres container manually |
| `make test-db-down` | Stop and remove the test Postgres container |
| `make lint` | Lint with ruff |
| `make docker-up` | Start Postgres + Redis + app via Docker Compose |
| `make docker-down` | Stop Docker Compose services |
| `make clean` | Remove `__pycache__` and `.pytest_cache` |

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | ✅ | PostgreSQL connection string |
| `REDIS_URL` | ✅ | Redis connection string |
| `SECRET_KEY` | ✅ | 32-byte hex string for session signing — generate with `secrets.token_hex(32)` |
| `FINNHUB_API_KEY` | ✅ | Finnhub REST API key — free tier is sufficient |
| `ANTHROPIC_API_KEY` | ✅ | Anthropic API key for Claude Haiku |
| `AI_PROVIDER` | ✅ | `anthropic` for production; any other value uses the fallback text analyser |
| `ENVIRONMENT` | ✅ | `development` or `production` — controls HTTPS-only cookies and SQL logging |

See `.env.example` for the full template. **Never commit `.env`.**

---

## Project Structure

```
├── app/
│   ├── main.py                  # FastAPI app, middleware stack, exception handlers
│   ├── config.py                # pydantic-settings env loader
│   ├── database.py              # async SQLAlchemy engine + session factory
│   ├── cache.py                 # Redis helpers: cache_get / cache_set / cache_delete
│   ├── limiter.py               # shared slowapi Limiter instance
│   ├── middleware/
│   │   ├── auth_guard.py        # get_current_user dependency + NotAuthenticatedException
│   │   └── csrf.py              # CSRF token middleware + csrf_protect dependency
│   ├── modules/
│   │   ├── auth/                # signup, login, logout, 2FA verify + bcrypt service
│   │   ├── stock/
│   │   │   ├── router.py        # quote lookup, favorites, all /api/stock/* endpoints
│   │   │   ├── service.py       # Finnhub + Yahoo Finance + KPI data fetchers
│   │   │   └── analysis.py      # AI analysis: targets + news + Reddit + Claude synthesis
│   │   ├── reddit/              # /api/reddit/summary — ApeWisdom + Claude buzz
│   │   ├── settings/            # 2FA enrol/disable routes
│   │   └── user/
│   │       ├── models.py        # User, UserFavorite, StockSearch, FavoriteHistory ORM models
│   │       └── repository.py    # all DB operations — no raw SQL anywhere
│   ├── templates/               # Jinja2 HTML templates (Tailwind CSS)
│   └── static/                  # images, favicon
├── tests/
│   ├── conftest.py              # shared fixtures: DB session, HTTP client, Redis mocks
│   ├── test_auth.py             # signup, login, session, redirect flows
│   ├── test_2fa.py              # TOTP enrol, enable, disable, verify flows
│   ├── test_api.py              # all /api/* JSON endpoints
│   ├── test_stock.py            # quote lookup, favorites, portfolio
│   └── test_repository.py       # direct DB layer tests — 25 cases, no HTTP
├── .github/
│   ├── workflows/
│   │   ├── ci.yml               # test + lint on every push and PR
│   │   └── codeql.yml           # SAST scanning on push/PR/weekly schedule
│   └── dependabot.yml           # weekly pip + Actions dependency updates
├── Dockerfile                   # multi-stage build, non-root user
├── docker-compose.yml           # local dev: Postgres + Redis
├── railway.toml                 # Railway deploy config (build, health check, restart policy)
├── Makefile                     # dev workflow shortcuts
├── SECURITY.md                  # security policy, implemented controls, contributor checklist
└── .env.example                 # environment variable template with placeholder values
```

## CICD Pipeline

<img width="2616" height="1304" alt="DevOps" src="https://github.com/user-attachments/assets/75303dd7-24ce-442b-a049-76e866d85c4d" />


---

## Testing

Tests run against a **real PostgreSQL database**. No SQLite, no mocking the ORM. Each test gets an isolated session that rolls back after completion, keeping tests independent.

```bash
make test-db-up   # starts throwaway Postgres on port 5433
make test         # runs full suite
make test-db-down # removes the container
```

### Coverage by layer (~85%)

| Layer | Approach |
|---|---|
| HTTP routes | `httpx.AsyncClient` with `ASGITransport` — full request/response cycle including middleware |
| Repository (DB) | Direct function calls against real Postgres — 25 cases in `test_repository.py` |
| Auth + 2FA | Full flow tests including TOTP verify with live `pyotp.TOTP` codes |
| External APIs | Patched at the service boundary — no live Finnhub or Anthropic calls in CI |
| Redis | `AsyncMock` — no Redis process needed to run tests |

CI runs the full suite on every push and PR using a Postgres service container (see `.github/workflows/ci.yml`).

The remaining ~15% is frontend JavaScript and Jinja2 template rendering. Adding Playwright E2E tests would be the next step for a production project — this tradeoff is discussed in the design decisions below.

---

## Design Decisions

**Why FastAPI over Flask/Django?**
FastAPI's async-first design matches this workload: every request fans out to 2–4 external APIs concurrently. `asyncio.gather` lets Finnhub, Yahoo Finance, ApeWisdom, and Anthropic calls run in parallel. A synchronous framework would serialise them, adding ~1–2 seconds of unnecessary latency per page load.

**Why bcrypt directly instead of passlib?**
passlib is a wrapper that adds abstraction at the cost of a harder dependency chain and occasional version conflicts. Using `bcrypt==4.2.0` directly is simpler, auditable, and explicit. The 72-byte input limit is enforced server-side to prevent a known bcrypt DoS vector.

**Why Redis cache-aside instead of always querying live?**
Finnhub's free tier is rate-limited. The most-used data such as top movers, KPI summary, Reddit sentiment all changes very frequently. A 5-minute TTL on quotes and 1-hour TTL on AI analysis gives a fast, consistent UX without burning API quota or adding latency on every page load.

**Why double-submit CSRF instead of synchroniser tokens?**
Synchroniser tokens require a DB or session read per request. Double-submit stores the token in the session (already in memory) and compares it to a hidden form field. Zero additional I/O. This is the approach used by Django, Rails, and Spring Security for the same reason.

**Why Claude Haiku over GPT-4o-mini?**
Haiku is faster (median ~400ms vs ~800ms), cheaper per token, and the task. The synthesising of 4 structured data points into 3–4 sentences doesn't require a frontier model. Results are cached for 1 hour, so the cost per user is negligible even at scale.

**Why no Playwright UI tests?**
For this scope, HTTP-layer tests give high signal with low infrastructure cost. Playwright requires a headed browser in CI, significantly increases test runtime, and primarily tests presentation rather than business logic. The ceiling on coverage is the frontend JS and templates. Adding Playwright smoke tests would be the next step in a production project.

---

## Deployment (Railway)

The app is pre-configured for Railway via `railway.toml`.

1. Push this repo to GitHub
2. Create a new Railway project and connect the repo
3. Add a **PostgreSQL** plugin and a **Redis** plugin from the Railway dashboard
4. Set the following in **Settings → Variables**:

```
SECRET_KEY=<generate: python -c "import secrets; print(secrets.token_hex(32))">
FINNHUB_API_KEY=<your key>
ANTHROPIC_API_KEY=<your key>
AI_PROVIDER=anthropic
ENVIRONMENT=production
```

`DATABASE_URL` and `REDIS_URL` are injected automatically by Railway's plugins. The health check at `/health` pings both Postgres and Redis and is used by Railway for deployment verification and restart decisions.

---

## Security

See [SECURITY.md](SECURITY.md) for the full security policy, all implemented controls, known limitations, and the contributor security checklist.
