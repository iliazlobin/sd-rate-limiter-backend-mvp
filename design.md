# Rate Limiter — MVP Design

Variant: **single-process, in-memory** (no Redis, no distributed coordination).
Full design: Notion `390d8650-05a8-81da-a188-f7f78732499c` (Redis Cluster, two-tier, 1M req/s).
This MVP implements the core token-bucket and sliding-window algorithms in-process so the algorithm logic,
header contracts, and acceptance suite transfer directly to the Redis-backed variant.

---

## 1. Requirements

### Functional
- FR1: Identify client from `X-API-Key` or `X-User-ID` header
- FR2: Enforce configurable rate-limit rules via token bucket algorithm
- FR3: Reject excess requests with HTTP 429 + `X-RateLimit-*` headers + `Retry-After`
- FR4: Support `token_bucket` and `sliding_window_counter` algorithms (per-rule selection)

### Non-functional
- NFR1: Sub-1ms p99 evaluation latency (in-process, no I/O)
- NFR2: Rule hot-reload via `PUT /rules/{id}` without restart
- NFR3: Thread-safe concurrent access (asyncio lock per bucket)
- NFR4: Zero external dependencies at runtime (no Redis, no DB)

### Out of scope
Distributed coordination, persistence across restarts, fail-open/fail-closed modes, per-endpoint rules, analytics.

---

## 2. Back of the envelope

- **Memory:** 1 bucket ≈ 120 bytes (key + tokens + timestamp + counters) × 10K active clients ≈ 1.2 MB → negligible for in-process
- **Latency:** in-memory dict lookup + arithmetic ≈ 2–5 µs → well under 1 ms budget
- **Throughput:** 500K checks/s on a single core (no I/O, no serialization) → single process sufficient for MVP

---

## 3. Data model

```
RateLimitRule {
  rule_id:      str           ← "user-100-per-min", "ip-20-per-sec"
  client_type:  "api_key" | "user_id"
  algorithm:    "token_bucket" | "sliding_window_counter"
  limit:        int           ← max allowed in window
  window_sec:   int           ← window size in seconds
  burst:        int | None    ← only for token_bucket (default = limit)
}

BucketState {
  key:          str           ← "{client_type}:{client_value}:{rule_id}"
  tokens:       float         ← current tokens (token_bucket)
  last_refill:  float         ← monotonic time of last refill (token_bucket)
  prev_count:   int           ← previous window count (sliding_window)
  curr_count:   int           ← current window count (sliding_window)
  curr_window:  int           ← current window index (sliding_window)
}
```

- `BucketState.key` is composite — same format as the full design's Redis key (hash-tag-ready for future sharding)
- Token bucket uses `time.monotonic()` for refill computation — no clock skew in single-process, but keeps the algorithm transferable
- Sliding window uses `time.time() // window_sec` for window indexing

---

## 4. API

- `POST /ratelimit/check` — evaluate one request against matching rules; body: `{"client_type": "user_id", "client_value": "42"}`, returns `{"allowed": true, "remaining": 95, "limit": 100, "reset_at": 1719792600}`
- `PUT /ratelimit/rules/{rule_id}` — create or update a rule (hot-reloaded into in-memory registry)
- `GET /ratelimit/rules` — list all active rules
- `DELETE /ratelimit/rules/{rule_id}` — remove a rule
- `GET /healthz` — liveness probe

### Rate-limit response headers (on every checked request, allow or deny)
- `X-RateLimit-Limit: 100`
- `X-RateLimit-Remaining: 95`
- `X-RateLimit-Reset: 1719792600`
- `Retry-After: 3` (only on 429; seconds until next token, or window reset)

---

## 5. High-Level Design

```mermaid
graph TB
    Client["Client"]
    MW["RateLimitMiddleware<br/>per-request interceptor"]
    Extractor["ClientExtractor<br/>X-API-Key / X-User-ID"]
    Registry["RuleRegistry<br/>in-memory rule store"]
    TB["TokenBucket<br/>algorithm service"]
    SW["SlidingWindow<br/>algorithm service"]
    Router["/ratelimit/*<br/>rule CRUD + healthz"]

    Client -->|HTTP request| MW
    MW --> Extractor
    Extractor -->|client_key| Registry
    Registry -->|matching rules| MW
    MW --> TB
    MW --> SW
    TB -->|{allowed, remaining}| MW
    SW -->|{allowed, remaining}| MW
    MW -->|allow or 429| Client
    Router --> Registry

    classDef edge fill:#fff3bf,stroke:#f08c00,color:#1a1a1a
    classDef svc fill:#d0ebff,stroke:#1c7ed6,color:#1a1a1a
    classDef algo fill:#d3f9d8,stroke:#2f9e44,color:#1a1a1a
    class Client edge
    class MW,Extractor,Registry,Router svc
    class TB,SW algo
```

The middleware intercepts every request, extracts the client identity, matches applicable rules, runs each rule's algorithm in-process, and either forwards the request or short-circuits with 429.

### FR1: Identify the client

**Flow:**
1. Middleware reads `X-API-Key` header → if present, `client_type = "api_key"`
2. Else reads `X-User-ID` header → if present, `client_type = "user_id"`
3. If neither header present, skip rate limiting (pass-through for anonymous traffic in MVP)
4. Construct composite key: `{client_type}:{client_value}:{rule_id}`

**Decision:** Two-header priority with `X-API-Key` taking precedence. This matches the full design's gateway-layer extraction pattern and keeps the key format hash-tag-ready.

### FR2: Token bucket enforcement

The token bucket stores `(tokens, last_refill)` per key. On each check:
1. Compute elapsed = `time.monotonic() - last_refill`
2. Refill: `tokens = min(burst, tokens + elapsed × rate)` where `rate = limit / window_sec`
3. If `tokens >= 1`: deduct 1, return allowed
4. Else: return denied

Thread safety: `asyncio.Lock` per bucket key. The lock is held only for the refill-check-deduct window (~microseconds).

**Design consideration:** Using `time.monotonic()` avoids wall-clock jumps (NTP, DST) corrupting refill computation. In the distributed variant, `redis.call('TIME')` serves the same role — the algorithm is the same, only the clock source changes.

### FR3: 429 response + headers

When a rule denies:
- HTTP status 429
- `X-RateLimit-Limit`: rule's `limit`
- `X-RateLimit-Remaining`: 0
- `X-RateLimit-Reset`: Unix timestamp when next token arrives or window resets
- `Retry-After`: seconds until retry is possible
- JSON body: `{"error": "rate_limited", "retry_after_ms": 3000}`

On allow, headers carry positive remaining count so clients can self-throttle.

### FR4: Algorithm selection

Each rule carries an `algorithm` field. The middleware dispatches to the correct service:

| Algorithm | Stored state | Memory per key | Use case |
|---|---|---|---|
| `token_bucket` | tokens (float) + last_refill (float) | ~24 bytes | User-facing API (burst-tolerant) |
| `sliding_window_counter` | prev_count + curr_count + curr_window (3 ints) | ~36 bytes | Per-IP abuse protection (near-exact) |

The sliding window counter uses Cloudflare's weighted-estimate formula:
```
weight = (window_sec - elapsed_in_window) / window_sec
estimated = prev_count × weight + curr_count
```
Achieves ~6% average drift with O(1) memory and no Lua dependency.

**Decision:** Both algorithms share the same `BucketState` struct; unused fields default to `None`. A future `gcra` algorithm would add a single `tat` field.

---

## 6. Deep dives

### DD1: In-memory thread safety without global lock

**Problem.** Multiple concurrent requests for the same client arrive at the same asyncio event-loop tick. Without coordination, two tasks read the same token count, both see enough tokens, and both decrement — a double-spend. In the distributed variant, Lua EVALSHA solves this by running atomically on Redis's single-threaded event loop. In-process, we need equivalent isolation.

**Approach 1: Global lock on the entire bucket store.**
One `asyncio.Lock` guards all bucket access. Every rate-limit check acquires it.
- **Pro:** Trivially correct. No per-key bookkeeping.
- **Con:** Serializes all rate-limit checks. At 10K concurrent clients, every check waits for every other check. Throughput collapses to ~1K checks/s — useless.

**Approach 2: Per-bucket `asyncio.Lock`.**
A dictionary of locks, keyed by bucket key. Each check acquires only its own bucket's lock.
```python
lock = locks.setdefault(key, asyncio.Lock())
async with lock:
    # refill, check, deduct
```
- **Pro:** Contention is per-client, not global. A busy client doesn't slow down idle clients. Throughput scales linearly with client count — 500K+ checks/s achievable.
- **Con:** Lock dictionary grows unboundedly. Need cleanup of locks for expired/inactive buckets.

**Approach 3: Lock-free with `asyncio` task isolation.**
Single asyncio event loop processes one coroutine at a time, and `dict.__getitem__` + arithmetic is a single Python bytecode sequence. If we structure the check as a single synchronous function with no `await` points, the event loop never yields mid-check.
- **Pro:** Zero lock overhead. Maximum throughput.
- **Con:** Brittle. Any future `await` (logging, metrics) breaks the guarantee. Hard to verify correctness as code evolves. Python's GIL doesn't help here — asyncio is cooperative, and an `await` anywhere in the call chain introduces a yield point.

**Decision:** Approach 2 (per-bucket `asyncio.Lock`). It is the closest analog to Lua atomicity on Redis's event loop — each bucket's state mutation is an isolated critical section. The lock dictionary is cleaned by a periodic sweep (every 60s, remove locks for buckets with no tokens and last access > 5 min ago).

**Rationale:** The same reasoning behind Lua EVALSHA applies here — per-key isolation is sufficient because each rule's allowance is an independent decision. The lock is held for ~5 µs (a few arithmetic ops), so even under hot-key contention (same client, 1K req/s), the wait time is negligible. Approach 1 is a non-starter for any non-trivial load. Approach 3 is tempting but fragile — the first teammate who adds `await log_metric()` silently introduces a race.

**Edge cases:**
- Lock dictionary memory: 10K locks × ~200 bytes = 2 MB. Negligible. The periodic sweep prevents unbounded growth.
- Deadlock: impossible — each check acquires exactly one lock, no nested acquisition.
- Lock not released on exception: `async with` guarantees release on any exit path.

### DD2: Sliding window counter — weighted estimate

**Problem.** The fixed-window counter (one integer, reset at boundary) has a known boundary-spike weakness: 100 requests at t=59 and 100 at t=61 both count against the same 60s window that reset at t=60, so 200 requests pass in 2 seconds without either window exceeding 100. The sliding window counter fixes this without storing per-request timestamps (which would be O(n) memory).

**Algorithm (Cloudflare's weighted-estimate approach):**

```
window_id   = floor(now / window_sec)
elapsed     = now % window_sec
weight      = (window_sec - elapsed) / window_sec
estimated   = prev_count × weight + curr_count
```

- `curr_count`: requests in the current (partial) window — incremented atomically
- `prev_count`: requests in the previous (complete) window — carried forward
- On window rollover: `prev_count = curr_count`, `curr_count = 1`, `curr_window = window_id`

**Decision:** Implemented as a standalone service (`SlidingWindowService`) with an `asyncio.Lock` per bucket. The algorithm needs only `INCR`-equivalent (Python `+= 1`) — the simplest possible operation — making it the fastest algorithm in the suite.

**Rationale:** Cloudflare measured 0.003% error rate across 400M requests from 270K sources. The 6% average drift is acceptable for abuse detection — the question "is this IP scraping?" has the same answer at 106/min vs 100/min. For exact quotas, use `token_bucket` with burst=limit.

**Edge cases:**
- First request for a key: `prev_count = 0`, `curr_count = 1`, `curr_window = W`. Estimated = 0×weight + 1.
- Window rollover with zero requests in new window yet: `curr_count = 1`, `prev_count = old_curr_count`.
- Clock jump backward (NTP): `window_id < curr_window` — treat as first-request, initialize fresh.

---

## 7. Trade-offs & decisions

| Decision | Choice | Rationale |
|---|---|---|
| Store | In-memory `dict` | MVP scope; no persistence needed. State loss on restart is acceptable (all buckets reset — fail-open behavior) |
| Thread safety | Per-bucket `asyncio.Lock` | Mirrors Redis Lua atomicity at in-process scale; per-key isolation prevents global serialization |
| Clock source | `time.monotonic()` | Immune to wall-clock jumps; equivalent to `redis.call('TIME')` in the distributed variant |
| Header priority | `X-API-Key` > `X-User-ID` | API keys are explicit auth; user IDs are derived from JWT — key-based rules are typically stricter |
| Sliding window impl | Weighted estimate (Cloudflare) | O(1) memory, O(1) time, 0.003% measured error — no Lua needed for MVP |
| Rule storage | In-memory dict + CRUD endpoints | Hot-reload via API; no config file watching needed for MVP |
| Missing headers | Pass-through (no rate limit) | MVP doesn't enforce authentication; production adds `X-Forwarded-For` fallback |
| Test isolation | `POST /ratelimit/rules/admin/reset` | Added during build — clears all non-default rules + bucket state between acceptance tests. Not in original design but required for reliable black-box testing against a shared server instance |

---

## 8. Module layout

```
sd-rate-limiter-backend-mvp-v2026.07.02.1/
├── design.md                          ← this file
├── README.md
├── AGENTS.md
├── pyproject.toml
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── src/rate_limiter/
│   ├── __init__.py
│   ├── main.py                        ← app factory create_app() + lifespan + /healthz
│   ├── config.py                      ← pydantic-settings (rate limit defaults, port)
│   ├── middleware.py                   ← RateLimitMiddleware (Starlette ASGI middleware)
│   ├── models/
│   │   ├── __init__.py
│   │   ├── rule.py                    ← RateLimitRule dataclass
│   │   └── bucket.py                  ← BucketState dataclass
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── check.py                   ← CheckRequest / CheckResponse pydantic models
│   │   └── rule.py                    ← RuleCreate / RuleResponse pydantic models
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── ratelimit.py               ← POST /ratelimit/check
│   │   └── rules.py                   ← CRUD /ratelimit/rules/* + POST /admin/reset
│   └── services/
│       ├── __init__.py
│       ├── extractor.py               ← ClientExtractor (header parsing)
│       ├── registry.py                ← RuleRegistry (in-memory rule store + hot-reload)
│       ├── token_bucket.py            ← TokenBucketService (refill-check-deduct)
│       └── sliding_window.py          ← SlidingWindowService (weighted estimate)
├── tests/
│   ├── conftest.py
│   └── test_healthz.py                ← only unit test (1 passing; algorithm/deep tests deferred)
└── verify/
    ├── manifest.env
    └── acceptance/
        ├── test_fr1_client_identification.py
        ├── test_fr2_token_bucket.py
        ├── test_fr3_rejection_headers.py
        └── test_fr4_algorithm_support.py
```

---

## 9. Implementation tasks (build breakdown)

Each task below is a kanban card for the build phase. Tagged with the appropriate engineer tier.
✅ = built and verified. ⏳ = deferred (planned but not shipped).

### Task 1: Project scaffold + config ✅
- Scaffold `pyproject.toml`, `.env.example`, `Dockerfile`, `docker-compose.yml`, `README.md`
- `src/rate_limiter/config.py` with pydantic-settings
- `src/rate_limiter/main.py` with `create_app()`, lifespan, `/healthz`
- **Tier: senior-engineer**

### Task 2: Data models + schemas ✅
- `src/rate_limiter/models/rule.py` — `RateLimitRule` dataclass
- `src/rate_limiter/models/bucket.py` — `BucketState` dataclass
- `src/rate_limiter/schemas/check.py` — `CheckRequest`, `CheckResponse`
- `src/rate_limiter/schemas/rule.py` — `RuleCreate`, `RuleResponse`
- **Tier: senior-engineer**

### Task 3: Client extraction + rule registry ✅
- `src/rate_limiter/services/extractor.py` — `ClientExtractor` (X-API-Key / X-User-ID, priority order)
- `src/rate_limiter/services/registry.py` — `RuleRegistry` (CRUD + match-by-client-type + `delete_all_except` for test isolation)
- **Tier: senior-engineer**

### Task 4: Token bucket algorithm (core) ✅
- `src/rate_limiter/services/token_bucket.py` — refill formula, per-bucket `asyncio.Lock`, `check()` returning `(allowed, remaining, reset_at)`
- Lock cleanup sweep (periodic, 60s)
- **Tier: staff-engineer** — correctness-critical (atomicity, lock lifecycle, refill math)

### Task 5: Sliding window algorithm (core) ✅
- `src/rate_limiter/services/sliding_window.py` — weighted-estimate formula, per-bucket `asyncio.Lock`, `check()` returning `(allowed, remaining, reset_at)`
- Edge cases: first request, window rollover, clock jump backward
- **Tier: staff-engineer** — correctness-critical (window arithmetic, edge cases, atomicity)

### Task 6: Rate-limit middleware ✅
- `src/rate_limiter/middleware.py` — ASGI middleware: extract → match rules → dispatch algorithm → set headers → allow/429
- Header construction: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, `Retry-After`
- 429 JSON body
- **Tier: senior-engineer**

### Task 7: Router wiring (HTTP layer) ✅
- `src/rate_limiter/routers/ratelimit.py` — `POST /ratelimit/check`
- `src/rate_limiter/routers/rules.py` — `GET/PUT/DELETE /ratelimit/rules`
- **Also added:** `POST /ratelimit/rules/admin/reset` (test isolation endpoint — not in original design)
- **Tier: senior-engineer**

### Task 8: White-box unit tests ⏳
- `tests/test_token_bucket.py` — refill math, burst cap, concurrency safety
- `tests/test_sliding_window.py` — weighted estimate, window rollover, edge cases
- `tests/test_extractor.py` — header priority, missing headers
- `tests/test_registry.py` — CRUD, match logic
- **Current state:** Only `tests/test_healthz.py` exists (1 passing). Algorithm and deep unit tests deferred.
- **Tier: senior-engineer**

### Task 9: Acceptance tests (black-box) ✅
- `verify/manifest.env` — e2e-verify contract
- `verify/acceptance/test_fr1_client_identification.py` — X-API-Key vs X-User-ID, missing headers
- `verify/acceptance/test_fr2_token_bucket.py` — burst, refill, exhaustion, 429
- `verify/acceptance/test_fr3_rejection_headers.py` — all 4 headers on allow and deny, Retry-After correctness
- `verify/acceptance/test_fr4_algorithm_support.py` — token_bucket vs sliding_window_counter, per-rule selection + CRUD
- **Tier: senior-engineer**

### Task 10: Docker + compose + deploy doc ✅
- Multi-stage Dockerfile (python:3.12-slim) with HEALTHCHECK + curl
- `docker-compose.yml` with named network, restart, env_file, x-app-vars anchor, Redis placeholder
- `DEPLOY.md` with env table, staging slot info, troubleshooting
- **Tier: senior-engineer**

### Task 11: CI pipeline ✅ (added during SRE polish)
- `.github/workflows/ci.yml` — 4 jobs: lint (ruff) / test (pytest) / docker (build) / e2e (start + accept + teardown)
- Uses `verify/manifest.env` for shared e2e configuration
- **Tier: senior-engineer**

---

## 10. Verification — evidence from build

### Lint
- **ruff check src/ tests/ verify/**: All checks passed — 0 issues (E, F, I, W)
- Verified against full codebase (12 source files + 5 test files + 1 conftest)

### Unit tests
- **1/1 passing** — `TestHealthz::test_returns_200` (GET /healthz → 200 + `{"status": "ok"}`)
- Algorithm unit tests (Task 8) were deferred; the existing `test_healthz.py` validates basic app bootstrapping

### Acceptance tests (4 suites, 1 test per FR)
| Suite | FR | What it covers | How it runs |
|-------|----|---------------|-------------|
| `test_fr1_client_identification.py` | FR1 | X-API-Key priority over X-User-ID; unknown client type pass-through | HTTP against live stack |
| `test_fr2_token_bucket.py` | FR2 | Burst allowance (5/5), exhaustion (6th denied), time-based refill (50ms) | HTTP against live stack |
| `test_fr3_rejection_headers.py` | FR3 | Allow/deny headers, reset_at in future, limit matches rule config | HTTP against live stack |
| `test_fr4_algorithm_support.py` | FR4 | Both algorithms, independence, no burst on sliding window, CRUD lifecycle | HTTP against live stack |

Each test auto-resets via `POST /ratelimit/rules/admin/reset` before running, guaranteeing isolation.

### Docker
- Multi-stage build compiles cleanly (python:3.12-slim)
- HEALTHCHECK curls `/healthz` every 5s with 8s startup grace
- `docker compose up -d --wait` blocks on healthy status
- PYTHONPATH fixed to `/app` (not `/app/src`) so `from src.rate_limiter.*` imports resolve correctly

### CI
- 4-job pipeline: lint (ruff) → test (pytest tests/) → docker (build) → e2e (start + acceptance + teardown)
- GitHub Actions `ubuntu-latest`, Python 3.12
- e2e job sources `verify/manifest.env` for shared configuration (PORT, UP, DOWN, READY, ACCEPTANCE)
- Schedule: daily at 06:00 UTC + on every push/PR

### Deviations from original design
1. **`POST /ratelimit/rules/admin/reset`** — added for test isolation. Not in original API spec.
2. **`RuleRegistry.delete_all_except()`** — helper method enabling the reset endpoint. Not in original service design.
3. **Unit tests deferred** — Task 8 (test_token_bucket.py, test_sliding_window.py, test_extractor.py, test_registry.py) not implemented. Only test_healthz.py shipped.
4. **CI pipeline** — not in original design; added during SRE polish phase.
