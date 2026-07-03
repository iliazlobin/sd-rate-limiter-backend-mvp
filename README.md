# Rate Limiter MVP

Single-process, in-memory rate limiter implementing **token bucket** and **sliding window counter** algorithms. No runtime dependencies beyond Python 3.11+ — no Redis, no database, no external services.

**Algorithms:**
- **Token bucket** — burst-tolerant, for user-facing API rate limits
- **Sliding window counter** (Cloudflare weighted-estimate) — near-exact, for abuse detection

## Quick start

```bash
cp .env.example .env
docker compose up -d --wait
curl http://localhost:8010/healthz
# → {"status":"ok"}
```

### Or run locally (without Docker)

```bash
pip install -e .
uvicorn src.rate_limiter.main:create_app --factory --host 0.0.0.0 --port 8000
```

## Architecture

```mermaid
graph TB
    Client["Client<br/>app / browser / service"]
    MW["RateLimitMiddleware<br/>per-request interceptor"]
    Extractor["ClientExtractor<br/>X-API-Key > X-User-ID"]
    Registry["RuleRegistry<br/>in-memory rule store<br/>hot-reload via CRUD"]
    TB["TokenBucketService<br/>refill-check-deduct"]
    SW["SlidingWindowService<br/>weighted estimate"]
    Router["/ratelimit/*<br/>rule CRUD + check"]

    Client -->|HTTP request| MW
    MW -->|extract headers| Extractor
    Extractor -->|client_key| Registry
    Registry -->|matching rules| MW
    MW --> TB
    MW --> SW
    TB -->|{allowed, remaining, reset}| MW
    SW -->|{allowed, remaining, reset}| MW
    MW -->|allow + headers| Client
    MW -->|429 + Retry-After| Client
    Router --> Registry

    classDef edge fill:#fff3bf,stroke:#f08c00,color:#1a1a1a
    classDef svc fill:#d0ebff,stroke:#1c7ed6,color:#1a1a1a
    classDef algo fill:#d3f9d8,stroke:#2f9e44,color:#1a1a1a
    class Client edge
    class MW,Extractor,Registry,Router svc
    class TB,SW algo
```

**Flow:** Every HTTP request hits the middleware → extracts `X-API-Key` or `X-User-ID` → matches active rules → runs each rule's algorithm (per-bucket `asyncio.Lock` for thread safety) → forwards the response with `X-RateLimit-*` headers, or short-circuits with HTTP 429.

## API

| Method | Path | Description |
|--------|------|-------------|
| GET    | `/healthz` | Liveness probe — returns `{"status": "ok"}` |
| POST   | `/ratelimit/check` | Evaluate a request against matching rules |
| PUT    | `/ratelimit/rules/{rule_id}` | Create or update a rate-limit rule (hot-reloaded) |
| GET    | `/ratelimit/rules` | List all active rules |
| DELETE | `/ratelimit/rules/{rule_id}` | Remove a rule |
| POST   | `/ratelimit/rules/admin/reset` | Reset all non-default rules + clear bucket state (test isolation) |

### Rate-limit headers

Every request (allowed or denied) carries:

- `X-RateLimit-Limit` — max requests per window
- `X-RateLimit-Remaining` — remaining requests before limit
- `X-RateLimit-Reset` — Unix timestamp when the window resets / next token arrives
- `Retry-After` — (on 429 only) seconds to wait before retrying

### Examples

**Create a rule:**

```json
PUT /ratelimit/rules/user-100-per-min
{
  "client_type": "user_id",
  "algorithm": "token_bucket",
  "limit": 100,
  "window_sec": 60,
  "burst": 100
}
```

**Check a request:**

```json
POST /ratelimit/check
{
  "client_type": "user_id",
  "client_value": "42"
}

// Response (200):
{
  "allowed": true,
  "remaining": 99,
  "limit": 100,
  "reset_at": 1719792600
}
```

**Denied (via middleware on real requests):**

```
HTTP/1.1 429 Too Many Requests
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1719792600
Retry-After: 3

{"error": "rate_limited", "retry_after_ms": 3000}
```

## Algorithms

### Token bucket (`token_bucket`)

Stores `(tokens, last_refill)` per key. On each check:

1. Compute elapsed = `time.monotonic() - last_refill`
2. Refill: `tokens = min(burst, tokens + elapsed × rate)` where `rate = limit / window_sec`
3. If `tokens >= cost`: deduct, allow. Else: deny.

**Use case:** User-facing API endpoints where short bursts are acceptable (a user clicking rapidly, a login page). Unused tokens accumulate up to `burst` capacity.

### Sliding window counter (`sliding_window_counter`)

Cloudflare's weighted-estimate formula — O(1) memory, ~6% average drift:

```
window_id   = floor(now / window_sec)
elapsed     = now % window_sec
weight      = (window_sec - elapsed) / window_sec
estimated   = prev_count × weight + curr_count
```

**Use case:** Per-IP abuse detection where strict boundaries matter. No burst behavior — every request counts against the window immediately.

## Configuration

All settings are environment-driven with prefix `RATELIMIT_`.

| Variable | Default | Description |
|----------|---------|-------------|
| `RATELIMIT_HOST` | `0.0.0.0` | Bind address |
| `RATELIMIT_PORT` | `8000` | In-container port |
| `RATELIMIT_DEFAULT_LIMIT` | `100` | Default max requests per window |
| `RATELIMIT_DEFAULT_WINDOW_SEC` | `60` | Default window size (seconds) |
| `RATELIMIT_DEFAULT_BURST` | `100` | Default burst capacity (token bucket) |
| `RATELIMIT_LOCK_CLEANUP_INTERVAL_SEC` | `60` | Cleanup sweep interval (seconds) |
| `RATELIMIT_BUCKET_IDLE_TTL_SEC` | `300` | Idle bucket TTL before cleanup |

## Testing

### White-box unit tests

```bash
pip install -e ".[dev]"
PYTHONPATH=. pytest tests/ -v
```

**Current status: 1/1 passing** (health check endpoint). The project ships with one unit test (`tests/test_healthz.py`) covering the liveness probe. Full algorithm unit tests (token bucket refill math, sliding window rollover, extractor priority, registry CRUD) were deferred to a follow-up — planned as `test_token_bucket.py`, `test_sliding_window.py`, `test_extractor.py`, `test_registry.py`.

### Black-box acceptance tests

Requires a running stack (Docker or local):

```bash
docker compose up -d --wait
API_BASE_URL=http://localhost:8010 pip install httpx pytest
pytest verify/acceptance/ -v
```

Four acceptance suites covering every functional requirement:

| File | FR | Tests |
|------|----|-------|
| `test_fr1_client_identification.py` | FR1 | Header priority (X-API-Key > X-User-ID), unknown client type |
| `test_fr2_token_bucket.py` | FR2 | Burst allowance, exhaustion, time-based refill |
| `test_fr3_rejection_headers.py` | FR3 | Allow headers, deny headers, reset_at in future, limit matching |
| `test_fr4_algorithm_support.py` | FR4 | Both algorithms, independence, burst vs no-burst, rule CRUD |

### CI pipeline

[![CI](https://github.com/iliazlobin/sd-rate-limiter-backend-mvp/actions/workflows/ci.yml/badge.svg)](https://github.com/iliazlobin/sd-rate-limiter-backend-mvp/actions/workflows/ci.yml)

`.github/workflows/ci.yml` runs 4 jobs on every push:

1. **Lint** — ruff check (E, F, I, W)
2. **Test** — pytest unit tests
3. **Docker** — `docker build` verifies the Dockerfile compiles
4. **e2e** — starts stack, runs all acceptance tests, tears down

**Lint status: clean** — 0 ruff issues across all source and test files.

## Project structure

```
├── src/rate_limiter/
│   ├── main.py              ← App factory (create_app), lifespan, /healthz
│   ├── config.py            ← pydantic-settings (RATELIMIT_* prefix)
│   ├── middleware.py         ← RateLimitMiddleware (ASGI interceptor)
│   ├── models/
│   │   ├── rule.py          ← RateLimitRule dataclass
│   │   └── bucket.py       ← BucketState dataclass (union struct)
│   ├── schemas/
│   │   ├── check.py        ← CheckRequest / CheckResponse
│   │   └── rule.py         ← RuleCreate / RuleResponse
│   ├── routers/
│   │   ├── ratelimit.py    ← POST /ratelimit/check
│   │   └── rules.py        ← CRUD /ratelimit/rules/*
│   └── services/
│       ├── extractor.py    ← ClientExtractor (header parsing)
│       ├── registry.py     ← RuleRegistry (in-memory rule store)
│       ├── token_bucket.py ← TokenBucketService (token bucket)
│       └── sliding_window.py  ← SlidingWindowService (weighted estimate)
├── tests/
│   ├── conftest.py         ← TestClient fixture
│   └── test_healthz.py     ← Health check test (1/1 passing)
├── verify/
│   ├── manifest.env        ← e2e-verify manifest
│   └── acceptance/
│       ├── conftest.py     ← Auto-reset before each test
│       ├── test_fr1_client_identification.py
│       ├── test_fr2_token_bucket.py
│       ├── test_fr3_rejection_headers.py
│       └── test_fr4_algorithm_support.py
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── DEPLOY.md
├── pyproject.toml
└── requirements.txt
```

## Deploy

See [DEPLOY.md](DEPLOY.md) for full instructions — Docker Compose, staging slot deployment via Hermes (ports 8001-8003), healthcheck, CI, and troubleshooting.

## Design

See [DESIGN.md](DESIGN.md) for the full design document — architecture overview, FR→acceptance-test traceability matrix, verification results (16/16 acceptance tests passing, CI pipeline). For deeper system-design deep dives, see [`design.md`](design.md).
