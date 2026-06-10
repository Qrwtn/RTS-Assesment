# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| `main`  | ✅ Yes    |
| All others | ❌ No |

Only the current `main` branch receives security fixes. There are no versioned releases at this time.

---

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

To report a vulnerability, email **tkemp@ippon.fr** with the subject line `[SECURITY] StockApp — <brief description>`. Include:

- A description of the vulnerability and its potential impact
- Steps to reproduce (proof-of-concept code or request/response samples if applicable)
- Affected component(s) (route, module, template, etc.)

You can expect an acknowledgement within **48 hours** and a resolution timeline within **7 days** for critical issues.

---

## Security Controls

The following controls are implemented in this application. They are documented here both for transparency and as a reference for contributors.

### Authentication & Session Management

- **Password hashing** — bcrypt via `bcrypt==4.2.0` directly (no passlib wrapper). A 72-byte input limit is enforced server-side to prevent bcrypt DoS via oversized inputs.
- **Session cookies** — Starlette `SessionMiddleware` with `same_site="lax"`, `https_only=True` in production, and a 24-hour `max_age`. Sessions are regenerated on login to prevent session fixation.
- **Logout** — session is explicitly cleared on logout; the logout route requires a valid CSRF token to prevent logout CSRF.
- **TOTP 2FA** — optional time-based one-time passwords via `pyotp`. Secrets are stored hashed in the database; QR codes are generated server-side and never stored.

### CSRF Protection

A double-submit session token pattern is used:
- A random token is generated per session and stored server-side in the session store.
- All state-changing form submissions (`POST`) must include a matching `csrf_token` hidden field.
- The `csrf_protect` FastAPI dependency enforces this on every mutating route.

### Rate Limiting

All endpoints are rate-limited via `slowapi` (a Starlette wrapper around `limits`):

| Endpoint | Limit |
|---|---|
| `POST /login` | 5 / minute |
| `POST /signup` | 5 / minute |
| `GET /api/stock/analysis/{symbol}` | 20 / minute |
| `GET /api/stock/history/{symbol}` | 30 / minute |
| `GET /api/stock/search` | 30 / minute |
| `GET /api/market/ticker` | 10 / minute |
| `GET /api/reddit/summary` | 10 / minute |

### Input Validation

- Stock ticker symbols on all routes are validated against `^[A-Z]{1,10}$` via FastAPI `Path` constraints — malformed input is rejected at the routing layer before reaching any external API.
- Search query length is capped at 50 characters in the service layer.
- Email format is validated server-side on signup (not just client-side).

### Security Headers

Every response includes the following headers (set in `app/main.py`):

```
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: geolocation=(), camera=(), microphone=()
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline' <cdn-origins>; ...
```

### Secrets Management

- All secrets (`SECRET_KEY`, `FINNHUB_API_KEY`, `ANTHROPIC_API_KEY`, `DATABASE_URL`, `REDIS_URL`) are loaded exclusively from environment variables via `pydantic-settings`.
- `.env` is in `.gitignore` and never committed. `.env.example` documents the required variables with placeholder values only.
- Each environment (development, production) uses a different `SECRET_KEY`.

### Dependency Scanning

- **Dependabot** is configured to open weekly PRs for vulnerable Python and GitHub Actions dependencies.
- **CodeQL** runs on every push and PR to scan for SAST issues (SQL injection, path traversal, XSS, SSRF, and 50+ other CWE categories).

---

## Known Limitations & Out of Scope

The following are known limitations acknowledged by design, not unresolved vulnerabilities:

- **CDN scripts without SRI hashes** — Tailwind, Chart.js, marked.js, and Lucide are loaded from CDN without Subresource Integrity hashes. This is a documented tradeoff for a development-stage project; SRI hashes should be added before a public production release.
- **`unsafe-inline` in CSP** — Inline `<script>` blocks in Jinja2 templates require `unsafe-inline`. Migrating to a nonce-based CSP would require a build pipeline to inject nonces at render time.
- **No account lockout** — Rate limiting on login is per-IP, not per-account. Distributed brute-force across many IPs is not prevented. This is acceptable for an assessment-scope application.
- **SQLAlchemy `echo=True` in development** — SQL queries are logged to stdout when `ENVIRONMENT=development`. This is intentional for debugging and is disabled in production.

---

## Security Checklist for Contributors

Before opening a PR that touches auth, routes, or templates:

- [ ] Does the new route need `current_user = Depends(get_current_user)`?
- [ ] Does the new form include `<input type="hidden" name="csrf_token" ...>`?
- [ ] Does the new POST route have `_csrf: None = Depends(csrf_protect)`?
- [ ] Does the new public API endpoint have a `@limiter.limit(...)` decorator?
- [ ] Are any path parameters validated with a `Path(pattern=...)` constraint?
- [ ] Are secrets read from `settings.*` and never hardcoded?
