"""
Vertex AI provider built on top of the unified `google-genai` SDK.

Default model is `gemini-3.1-pro` (Feb 2026 release, 1M context, native
structured JSON output). Credentials are resolved in this order:

1. `GOOGLE_APPLICATION_CREDENTIALS_JSON` - raw JSON string in env
   (preferred on Heroku since the dyno has no persistent disk).
2. `GOOGLE_APPLICATION_CREDENTIALS` - filesystem path to a key file
   (used in local dev).
3. Application Default Credentials (gcloud auth, GCE metadata, etc.).

Pricing tracker is intentionally conservative and overridable via env
so we can adjust without a code release.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from .base import BaseProvider, ChatRequest, ChatResponse

logger = logging.getLogger(__name__)


# Per-1K-token pricing (USD). Update when Google publishes new rates.
# Override at runtime via VERTEX_PRICE_*_PER_1K env vars.
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "gemini-3.1-pro": {"input": 0.00125, "output": 0.01000},
    "gemini-3.1-pro-preview": {"input": 0.00125, "output": 0.01000},
    "gemini-3-pro": {"input": 0.00125, "output": 0.01000},
    "gemini-3-pro-preview": {"input": 0.00125, "output": 0.01000},
    "gemini-2.5-pro": {"input": 0.00125, "output": 0.01000},
    "gemini-2.5-flash": {"input": 0.00010, "output": 0.00040},
    "gemini-3.1-flash": {"input": 0.00010, "output": 0.00040},
    "gemini-3.1-flash-preview": {"input": 0.00010, "output": 0.00040},
}


def _resolve_credentials():
    """Build google.oauth2 credentials from env, supporting Heroku-style
    JSON-in-env or filesystem path. Returns None to fall back to ADC."""
    raw_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()
    if raw_json:
        try:
            from google.oauth2 import service_account  # type: ignore

            info = json.loads(raw_json)
            return service_account.Credentials.from_service_account_info(
                info,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        except Exception as exc:
            logger.warning("Failed to parse GOOGLE_APPLICATION_CREDENTIALS_JSON: %s", exc)

    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if path and os.path.exists(path):
        try:
            from google.oauth2 import service_account  # type: ignore

            return service_account.Credentials.from_service_account_file(
                path,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        except Exception as exc:
            logger.warning("Failed to load credentials from %s: %s", path, exc)

    return None


class VertexProvider(BaseProvider):
    name = "vertex"

    def __init__(
        self,
        *,
        model: str = "gemini-3.1-pro",
        project: str | None = None,
        location: str | None = None,
    ) -> None:
        super().__init__(model=model)
        self.project = project or os.environ.get("GOOGLE_VERTEX_PROJECT_ID", "")
        self.location = location or os.environ.get(
            "GOOGLE_VERTEX_LOCATION", "us-central1"
        )
        self._client = None

    def _client_lazy(self):
        if self._client is not None:
            return self._client
        try:
            from google import genai  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "google-genai is not installed. Add `google-genai` to requirements.txt."
            ) from exc
        if not self.project:
            raise RuntimeError(
                "GOOGLE_VERTEX_PROJECT_ID is not set; cannot initialise Vertex client."
            )
        creds = _resolve_credentials()
        client_kwargs: dict[str, Any] = {
            "vertexai": True,
            "project": self.project,
            "location": self.location,
        }
        if creds is not None:
            client_kwargs["credentials"] = creds
        self._client = genai.Client(**client_kwargs)
        return self._client

    def _pricing(self) -> dict[str, float]:
        defaults = DEFAULT_PRICING.get(self.model, {"input": 0.0, "output": 0.0})
        try:
            override_in = os.environ.get(
                f"VERTEX_PRICE_INPUT_PER_1K_{self.model.upper().replace('.', '_').replace('-', '_')}"
            )
            override_out = os.environ.get(
                f"VERTEX_PRICE_OUTPUT_PER_1K_{self.model.upper().replace('.', '_').replace('-', '_')}"
            )
            return {
                "input": float(override_in) if override_in else defaults["input"],
                "output": float(override_out) if override_out else defaults["output"],
            }
        except (TypeError, ValueError):
            return defaults

    def chat(self, request: ChatRequest) -> ChatResponse:
        from google.genai import types  # type: ignore

        client = self._client_lazy()

        config_kwargs: dict[str, Any] = {
            "temperature": request.temperature,
            "max_output_tokens": request.max_output_tokens,
        }
        if request.system_instruction:
            config_kwargs["system_instruction"] = request.system_instruction
        if request.schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = request.schema

        config = types.GenerateContentConfig(**config_kwargs)

        started = time.monotonic()
        response = client.models.generate_content(
            model=self.model,
            contents=request.prompt,
            config=config,
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)

        text = (response.text or "").strip()
        parsed: Any | None = None
        if request.schema is not None and text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None

        usage = getattr(response, "usage_metadata", None)
        input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)

        pricing = self._pricing()
        cost = (input_tokens / 1000) * pricing["input"] + (
            output_tokens / 1000
        ) * pricing["output"]

        raw: dict[str, Any] = {
            "model": self.model,
            "elapsed_ms": elapsed_ms,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        }

        return ChatResponse(
            text=text,
            parsed=parsed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            raw=raw,
        )
