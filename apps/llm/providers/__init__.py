"""LLM provider registry."""
from __future__ import annotations

from django.conf import settings

from .base import BaseProvider, ChatRequest, ChatResponse
from .vertex import VertexProvider


def get_provider(name: str | None = None, model: str | None = None) -> BaseProvider:
    """Return an instance of the requested provider, defaulting to settings."""
    name = (name or getattr(settings, "LLM_DEFAULT_PROVIDER", "vertex")).lower()
    if name == "vertex":
        return VertexProvider(
            model=model or getattr(settings, "LLM_DEFAULT_MODEL", "gemini-3.1-pro"),
        )
    raise ValueError(
        f"Unknown LLM provider: {name!r}. Currently supported: vertex."
    )


__all__ = [
    "BaseProvider",
    "ChatRequest",
    "ChatResponse",
    "VertexProvider",
    "get_provider",
]
