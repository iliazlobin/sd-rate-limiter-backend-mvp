"""FR2: In-memory rule registry — CRUD + client-type matching.

Rules are hot-reloaded via PUT/DELETE endpoints. Thread-safe via asyncio.Lock.
"""

import asyncio

from src.rate_limiter.models.rule import RateLimitRule


class RuleRegistry:
    """Thread-safe in-memory rule store.

    Supports hot-reload: PUT creates/updates, DELETE removes.
    match() returns all rules applicable to a given client_type.
    """

    def __init__(self):
        self._rules: dict[str, RateLimitRule] = {}
        self._lock = asyncio.Lock()

    async def upsert(
        self,
        rule_id: str,
        client_type: str,
        algorithm: str,
        limit: int,
        window_sec: int,
        burst: int | None = None,
    ) -> None:
        async with self._lock:
            self._rules[rule_id] = RateLimitRule(
                rule_id=rule_id,
                client_type=client_type,  # type: ignore[arg-type]
                algorithm=algorithm,  # type: ignore[arg-type]
                limit=limit,
                window_sec=window_sec,
                burst=burst,
            )

    def get(self, rule_id: str) -> RateLimitRule | None:
        return self._rules.get(rule_id)

    def delete(self, rule_id: str) -> bool:
        if rule_id in self._rules:
            del self._rules[rule_id]
            return True
        return False

    def delete_all_except(self, rule_ids: set[str]) -> int:
        """Delete all rules except those with ids in `rule_ids`. Returns count deleted."""
        to_delete = [k for k in self._rules if k not in rule_ids]
        for k in to_delete:
            del self._rules[k]
        return len(to_delete)

    def match(self, client_type: str) -> list[RateLimitRule]:
        """Return all rules matching the given client_type."""
        return [r for r in self._rules.values() if r.client_type == client_type]

    def list_all(self) -> list[RateLimitRule]:
        return list(self._rules.values())
