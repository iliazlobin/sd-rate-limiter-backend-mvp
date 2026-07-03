"""ASGI middleware — intercepts every request, runs rate-limit checks."""

from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.rate_limiter.services.extractor import ClientExtractor
from src.rate_limiter.services.registry import RuleRegistry
from src.rate_limiter.services.sliding_window import SlidingWindowService
from src.rate_limiter.services.token_bucket import TokenBucketService


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Intercepts every request, extracts client identity, runs matching rules.

    Dispatches to TokenBucketService or SlidingWindowService based on rule algorithm.
    Sets X-RateLimit-* headers on every request (allow or deny).
    Returns 429 JSON on denial.
    """

    def __init__(
        self,
        app,
        registry: RuleRegistry,
        extractor: ClientExtractor | None = None,
        token_bucket: TokenBucketService | None = None,
        sliding_window: SlidingWindowService | None = None,
    ):
        super().__init__(app)
        self.registry = registry
        self.extractor = extractor or ClientExtractor()
        self.token_bucket = token_bucket or TokenBucketService()
        self.sliding_window = sliding_window or SlidingWindowService()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip rate limiting for management endpoints
        if request.url.path.startswith("/ratelimit/") or request.url.path == "/healthz":
            return await call_next(request)

        # FR1: extract client identity
        client = self.extractor.extract(request)
        if client is None:
            # No identity header — pass through (MVP behavior)
            return await call_next(request)

        # Find matching rules
        rules = self.registry.match(client_type=client.client_type)
        if not rules:
            return await call_next(request)

        # Evaluate each rule; deny if any rule rejects
        deny_reason: tuple[int, int, int] | None = None  # (remaining, limit, reset_at)
        last_result: tuple[bool, int, int] | None = None  # fallback for allowed path headers
        for rule in rules:
            if rule.algorithm == "token_bucket":
                result = await self.token_bucket.check(
                    key=f"{client.client_type}:{client.client_value}:{rule.rule_id}",
                    rate=rule.limit / rule.window_sec,
                    burst=rule.burst or rule.limit,
                    cost=1,
                )
            elif rule.algorithm == "sliding_window_counter":
                result = await self.sliding_window.check(
                    key=f"{client.client_type}:{client.client_value}:{rule.rule_id}",
                    limit=rule.limit,
                    window_sec=rule.window_sec,
                )
            else:
                continue

            last_result = result
            allowed, remaining, reset_at = result
            if not allowed:
                deny_reason = (remaining, rule.limit, reset_at)
                break

        # FR3: construct response with headers
        if deny_reason is not None:
            remaining, limit, reset_at = deny_reason
            retry_after = max(1, reset_at - int(__import__("time").time()))
            return self._rate_limited_response(limit, remaining, reset_at, retry_after)

        # Allowed — forward to upstream with rate-limit headers
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(rules[0].limit)
        if last_result is not None:
            _, remaining, reset_at = last_result
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            response.headers["X-RateLimit-Reset"] = str(reset_at)
        return response

    def _rate_limited_response(
        self, limit: int, remaining: int, reset_at: int, retry_after: int
    ) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limited",
                "retry_after_ms": retry_after * 1000,
            },
            headers={
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": str(remaining),
                "X-RateLimit-Reset": str(reset_at),
                "Retry-After": str(retry_after),
            },
        )
