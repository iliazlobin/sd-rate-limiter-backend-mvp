"""Request/response schemas for rule CRUD endpoints."""

from typing import Literal

from pydantic import BaseModel, Field


class RuleCreate(BaseModel):
    client_type: Literal["api_key", "user_id"]
    algorithm: Literal["token_bucket", "sliding_window_counter"]
    limit: int = Field(..., gt=0)
    window_sec: int = Field(..., gt=0)
    burst: int | None = Field(None, gt=0)


class RuleResponse(BaseModel):
    rule_id: str
    client_type: str
    algorithm: str
    limit: int
    window_sec: int
    burst: int | None = None
