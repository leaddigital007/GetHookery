"""
Provider-agnostic interface so we can swap Vertex / OpenAI without
touching call sites. All providers must return the same `ChatResponse`
shape so the LLMService can hash, audit and cost them uniformly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatRequest:
    prompt: str
    schema: dict[str, Any] | None = None
    system_instruction: str | None = None
    temperature: float = 0.1
    max_output_tokens: int = 2048


@dataclass
class ChatResponse:
    text: str
    parsed: Any | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


class BaseProvider:
    """Common provider interface."""

    name: str = "base"

    def __init__(self, *, model: str) -> None:
        self.model = model

    def chat(self, request: ChatRequest) -> ChatResponse:  # pragma: no cover
        raise NotImplementedError
