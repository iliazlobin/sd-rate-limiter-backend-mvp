"""Rate Limiter — single-process, in-memory MVP.

Core package. Exports the app factory for ASGI servers.
"""

from src.rate_limiter.main import create_app

__all__ = ["create_app"]
