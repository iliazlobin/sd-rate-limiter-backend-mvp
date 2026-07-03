"""CRUD endpoints for rate-limit rules — GET / PUT / DELETE /ratelimit/rules.

Thin HTTP layer: validate input, delegate to RuleRegistry, return DTOs.
"""

from fastapi import APIRouter, HTTPException, Request

from src.rate_limiter.schemas.rule import RuleCreate, RuleResponse

router = APIRouter(prefix="/ratelimit/rules", tags=["rules"])


@router.get("", response_model=list[RuleResponse])
async def list_rules(request: Request) -> list[RuleResponse]:
    """List all active rate-limit rules."""
    registry = request.app.state.registry
    return [
        RuleResponse(
            rule_id=r.rule_id,
            client_type=r.client_type,
            algorithm=r.algorithm,
            limit=r.limit,
            window_sec=r.window_sec,
            burst=r.burst,
        )
        for r in registry.list_all()
    ]


@router.put("/{rule_id}", response_model=RuleResponse, status_code=201)
async def upsert_rule(rule_id: str, body: RuleCreate, request: Request) -> RuleResponse:
    """Create or update a rate-limit rule. Hot-reloaded into the active registry."""
    registry = request.app.state.registry
    await registry.upsert(
        rule_id=rule_id,
        client_type=body.client_type,
        algorithm=body.algorithm,
        limit=body.limit,
        window_sec=body.window_sec,
        burst=body.burst,
    )
    rule = registry.get(rule_id)
    if rule is None:
        raise HTTPException(status_code=500, detail="Rule creation failed")
    return RuleResponse(
        rule_id=rule.rule_id,
        client_type=rule.client_type,
        algorithm=rule.algorithm,
        limit=rule.limit,
        window_sec=rule.window_sec,
        burst=rule.burst,
    )


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(rule_id: str, request: Request) -> None:
    """Remove a rate-limit rule from the active registry."""
    registry = request.app.state.registry
    if not registry.delete(rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")


@router.post("/admin/reset", status_code=200)
async def reset_rules(request: Request) -> dict:
    """Delete all non-default rules and clear all bucket state (acceptance test isolation)."""
    registry = request.app.state.registry
    deleted = registry.delete_all_except({"default"})

    # Clear accumulated bucket state from previous test runs
    token_bucket = request.app.state.token_bucket
    sliding_window = request.app.state.sliding_window
    token_bucket.clear()
    sliding_window.clear()

    return {"deleted": deleted}
