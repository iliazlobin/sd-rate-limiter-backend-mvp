"""Rate limit rule definition — matches the full design's RateLimitRule entity."""

from dataclasses import dataclass
from typing import Literal


@dataclass
class RateLimitRule:
    rule_id: str
    client_type: Literal["api_key", "user_id"]
    algorithm: Literal["token_bucket", "sliding_window_counter"]
    limit: int  # max requests per window
    window_sec: int  # window size in seconds
    burst: int | None = None  # only for token_bucket; defaults to limit if unset

    def __post_init__(self):
        if self.burst is None and self.algorithm == "token_bucket":
            self.burst = self.limit
