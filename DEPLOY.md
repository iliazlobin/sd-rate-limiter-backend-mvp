# Deploy — Rate Limiter MVP

## Prerequisites

- **Docker & Docker Compose** (Compose V2, included with Docker Desktop / Colima)

## Quick start

```bash
# 1. Create environment file (edit if needed)
cp .env.example .env

# 2. Start the service
docker compose up -d --wait

# 3. Verify health
curl http://localhost:${APP_PORT:-8010}/healthz
# → {"status":"ok"}
```

## Configuration

All settings are environment-driven. Copy `.env.example` to `.env` and adjust, or
pass variables inline. See below for the complete reference.

| Variable | Default | Description |
|---|---|---|
| `APP_PORT` | `8010` | Host port mapped to the container (override for stg slots: 8001/8002/8003) |
| `RATELIMIT_HOST` | `0.0.0.0` | Container bind address |
| `RATELIMIT_PORT` | `8000` | Container listen port |
| `RATELIMIT_DEFAULT_LIMIT` | `100` | Max requests per client per window |
| `RATELIMIT_DEFAULT_WINDOW_SEC` | `60` | Rate-limit window in seconds |
| `RATELIMIT_DEFAULT_BURST` | `100` | Token bucket burst allowance |
| `RATELIMIT_LOCK_CLEANUP_INTERVAL_SEC` | `60` | Cleanup sweep interval (seconds) |
| `RATELIMIT_BUCKET_IDLE_TTL_SEC` | `300` | Idle bucket TTL before cleanup (seconds) |

## Testing

```bash
# Unit tests
docker compose exec app pytest tests/ -v

# Acceptance tests
docker compose exec app pytest verify/acceptance/ -v

# Everything
docker compose exec app pytest tests/ verify/acceptance/ -v
```

## Logs

```bash
# Follow logs
docker compose logs -f

# Last 100 lines
docker compose logs --tail=100

# Specific service
docker compose logs app
```

## Build (without Compose)

```bash
docker build -t rate-limiter .
docker run -p 8010:8000 \
  -e RATELIMIT_DEFAULT_LIMIT=100 \
  rate-limiter
```

## Staging slot deployment

When exposing via the `hermes-stg{1,2,3}.iliazlobin.com` slots, set the host port
to match the slot:

```bash
APP_PORT=8001 docker compose up -d --wait
# Accessible at https://hermes-stg1.iliazlobin.com/healthz
```

The Caddy reverse-proxy in the Hermes sandbox routes `hermes-stgN.iliazlobin.com`
→ `127.0.0.1:800N`. Only these three Cloudflare-Access-gated URLs are permitted.

## Healthcheck

The container includes a Docker HEALTHCHECK that curls `/healthz` every 5s.
`docker compose up --wait` blocks until the container reports healthy.

```bash
# Manual check
curl -sI http://localhost:${APP_PORT:-8010}/healthz
# HTTP/1.1 200 OK

curl -s http://localhost:${APP_PORT:-8010}/healthz
# {"status":"ok"}
```

## Stop & clean

```bash
docker compose down --volumes
docker compose down -v  # shorthand
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Container exits immediately | Port conflict | Change `APP_PORT` |
| `curl: (7) Failed to connect` | Service not ready | Wait for `--wait` or check `docker compose ps` |
| `{"detail":"Not Found"}` on `/healthz` | Wrong PYTHONPATH | Rebuild with the included Dockerfile |
| `ModuleNotFoundError: src.rate_limiter.*` | PYTHONPATH mismatch | Set `PYTHONPATH=/app` if running outside Docker |
| Docker build slow | No cache | Use `docker build --pull` periodically |

## CI

The CI pipeline (`.github/workflows/ci.yml`) runs on every push:

1. **Lint** — ruff check (E, F, I, W)
2. **Test** — pytest (unit + acceptance)
3. **Docker build** — verifies the Dockerfile compiles

To run CI checks locally:

```bash
ruff check src/ tests/ verify/
pytest tests/ verify/acceptance/ -v
docker build -t rate-limiter .
```
