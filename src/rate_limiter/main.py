"""Application factory with lifespan, healthz, and router mounting."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from src.rate_limiter.middleware import RateLimitMiddleware
from src.rate_limiter.routers.ratelimit import router as ratelimit_router
from src.rate_limiter.routers.rules import router as rules_router
from src.rate_limiter.services.registry import RuleRegistry
from src.rate_limiter.services.sliding_window import SlidingWindowService
from src.rate_limiter.services.token_bucket import TokenBucketService


def _create_services(app: FastAPI) -> None:
    """Create and wire services into app.state."""
    registry = RuleRegistry()
    token_bucket = TokenBucketService()
    sliding_window = SlidingWindowService()

    app.state.registry = registry
    app.state.token_bucket = token_bucket
    app.state.sliding_window = sliding_window


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: seed default rules, start cleanup. Shutdown: clean up tasks."""
    registry: RuleRegistry = app.state.registry
    token_bucket: TokenBucketService = app.state.token_bucket
    sliding_window: SlidingWindowService = app.state.sliding_window

    # Seed default rule for MVP smoke test
    await registry.upsert(
        rule_id="default",
        client_type="user_id",
        algorithm="token_bucket",
        limit=100,
        window_sec=60,
        burst=100,
    )

    # Start cleanup sweeps
    await token_bucket.start_cleanup()
    await sliding_window.start_cleanup()

    yield

    # Shutdown: cancel cleanup tasks
    if token_bucket._cleanup_task:
        token_bucket._cleanup_task.cancel()
    if sliding_window._cleanup_task:
        sliding_window._cleanup_task.cancel()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Rate Limiter MVP",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Create services before adding middleware — middleware constructor
    # receives live references; lifespan later starts cleanup tasks.
    _create_services(app)

    # Wire up rate-limit middleware
    app.add_middleware(
        RateLimitMiddleware,
        registry=app.state.registry,
        token_bucket=app.state.token_bucket,
        sliding_window=app.state.sliding_window,
    )

    @app.get("/healthz")
    async def healthz():
        return JSONResponse({"status": "ok"})

    app.include_router(ratelimit_router)
    app.include_router(rules_router)

    return app
