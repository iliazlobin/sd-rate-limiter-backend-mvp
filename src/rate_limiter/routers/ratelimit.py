"""POST /ratelimit/check — evaluate a request against matching rules.

Thin HTTP layer: parse request, dispatch to services, serialize response.
"""

from fastapi import APIRouter, Request

from src.rate_limiter.schemas.check import CheckRequest, CheckResponse

router = APIRouter(prefix="/ratelimit", tags=["ratelimit"])


@router.post("/check", response_model=CheckResponse)
async def check_rate_limit(body: CheckRequest, request: Request) -> CheckResponse:
    """Evaluate one request against all matching rate-limit rules.

    Returns allowed=true/false with remaining count and reset timestamp.
    On deny, the middleware (not this endpoint) sets HTTP 429 and headers.
    This endpoint is for programmatic checks (e.g. from other services).
    """
    registry = request.app.state.registry

    rules = registry.match(client_type=body.client_type)
    if not rules:
        return CheckResponse(allowed=True, remaining=0, limit=0, reset_at=0)

    # Evaluate each rule — deny if any rejects; track last allowed for aggregate response
    last_allowed: tuple[int, int, int] | None = None
    for rule in rules:
        if rule.algorithm == "token_bucket":
            bucket_service = request.app.state.token_bucket
            allowed, remaining, reset_at = await bucket_service.check(
                key=f"{body.client_type}:{body.client_value}:{rule.rule_id}",
                rate=rule.limit / rule.window_sec,
                burst=rule.burst or rule.limit,
                cost=1,
            )
        elif rule.algorithm == "sliding_window_counter":
            sliding_service = request.app.state.sliding_window
            allowed, remaining, reset_at = await sliding_service.check(
                key=f"{body.client_type}:{body.client_value}:{rule.rule_id}",
                limit=rule.limit,
                window_sec=rule.window_sec,
            )
        else:
            continue

        if not allowed:
            return CheckResponse(
                allowed=False, remaining=remaining, limit=rule.limit, reset_at=reset_at
            )
        last_allowed = (remaining, rule.limit, reset_at)

    if last_allowed is not None:
        remaining, limit, reset_at = last_allowed
        return CheckResponse(allowed=True, remaining=remaining, limit=limit, reset_at=reset_at)

    return CheckResponse(allowed=True, remaining=0, limit=0, reset_at=0)
