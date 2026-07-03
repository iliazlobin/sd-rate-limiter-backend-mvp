"""FR1: Client identity extraction from HTTP headers.

Priority: X-API-Key > X-User-ID. Returns None if neither header present (pass-through).
"""

from dataclasses import dataclass

from starlette.requests import Request


@dataclass
class ClientIdentity:
    client_type: str  # "api_key" or "user_id"
    client_value: str  # the actual key / user ID value


class ClientExtractor:
    """Extracts client identity from request headers.

    Priority: X-API-Key (explicit auth) > X-User-ID (derived from JWT).
    Returns None for anonymous/headerless requests (MVP pass-through behavior).
    """

    API_KEY_HEADER = "X-API-Key"
    USER_ID_HEADER = "X-User-ID"

    def extract(self, request: Request) -> ClientIdentity | None:
        api_key = request.headers.get(self.API_KEY_HEADER)
        if api_key:
            return ClientIdentity(client_type="api_key", client_value=api_key)

        user_id = request.headers.get(self.USER_ID_HEADER)
        if user_id:
            return ClientIdentity(client_type="user_id", client_value=user_id)

        return None
