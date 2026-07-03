"""Request/response schemas for POST /ratelimit/check."""

from pydantic import BaseModel, Field


class CheckRequest(BaseModel):
    client_type: str = Field(..., description="'api_key' or 'user_id'")
    client_value: str = Field(..., description="The actual key or user ID value")


class CheckResponse(BaseModel):
    allowed: bool
    remaining: int = 0
    limit: int = 0
    reset_at: int = 0  # Unix timestamp
