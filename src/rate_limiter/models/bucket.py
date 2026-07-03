"""In-memory bucket state — union struct for token_bucket and sliding_window_counter.

Matches the full design's BucketState entity. Unused fields are None.
"""

from dataclasses import dataclass


@dataclass
class BucketState:
    key: str  # composite: "{client_type}:{client_value}:{rule_id}"
    tokens: float | None = None  # token_bucket: current token count
    last_refill: float | None = None  # token_bucket: monotonic time of last refill
    prev_count: int | None = None  # sliding_window: previous complete window count
    curr_count: int | None = None  # sliding_window: current partial window count
    curr_window: int | None = None  # sliding_window: current window index
