"""
High-level wrapper around any LLM provider that:

  - hashes the input and reuses prior LLMCall rows (idempotency)
  - records every call in LLMCall (audit + cost)
  - enforces a daily USD ceiling so a misbehaving job cannot drain the
    account while we sleep (circuit breaker)
  - exposes one `run(...)` method that takes a Django model target,
    a task label and a prompt; the caller does not need to know which
    provider is in use.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, Callable, Iterable, Iterator

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, connections
from django.db import models as dj_models
from django.db.models import Sum
from django.utils import timezone

from .models import LLMCall, LLMCallStatus, LLMProvider, LLMTask
from .providers import ChatRequest, get_provider

logger = logging.getLogger(__name__)


class LLMBudgetExceeded(RuntimeError):
    """Raised when the daily LLM budget would be exceeded by the next call."""


@dataclass
class LLMResult:
    """Caller-facing return type for `LLMService.run(...)`."""

    text: str
    parsed: Any | None
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cached: bool
    call: LLMCall


class LLMService:
    def __init__(
        self,
        *,
        provider_name: str | None = None,
        model: str | None = None,
        daily_budget_usd: float | None = None,
    ) -> None:
        self.provider = get_provider(name=provider_name, model=model)
        self.provider_name = self.provider.name
        self.model = self.provider.model
        env_budget = getattr(settings, "LLM_MAX_DAILY_USD", 5.0)
        self.daily_budget_usd = (
            float(daily_budget_usd) if daily_budget_usd is not None else float(env_budget)
        )

    def _today_spend(self) -> float:
        since = timezone.now() - timedelta(days=1)
        agg = LLMCall.objects.filter(
            created_at__gte=since,
            status=LLMCallStatus.SUCCESS,
        ).aggregate(total=Sum("cost_usd"))
        return float(agg["total"] or 0.0)

    def _check_budget(self) -> None:
        spent = self._today_spend()
        if spent >= self.daily_budget_usd:
            raise LLMBudgetExceeded(
                f"Daily LLM budget of ${self.daily_budget_usd:.2f} reached "
                f"(${spent:.2f} spent in the last 24h). "
                "Raise LLM_MAX_DAILY_USD if this is expected."
            )

    def run(
        self,
        *,
        task: str,
        prompt: str,
        schema: dict[str, Any] | None = None,
        system_instruction: str | None = None,
        target: dj_models.Model | None = None,
        temperature: float = 0.1,
        max_output_tokens: int = 2048,
        schema_version: str = "v1",
        import_run=None,
        force_refresh: bool = False,
    ) -> LLMResult:
        """Run the prompt, returning a cached result if available."""
        full_prompt = prompt
        if system_instruction:
            full_prompt = f"[SYSTEM]\n{system_instruction}\n\n[USER]\n{prompt}"

        input_hash = LLMCall.hash_input(
            provider=self.provider_name,
            model=self.model,
            task=task,
            prompt_text=full_prompt,
            schema_version=schema_version,
        )

        prior = (
            LLMCall.objects.filter(
                input_hash=input_hash,
                provider=self.provider_name,
                model=self.model,
                task=task,
            )
            .order_by("-created_at")
            .first()
        )

        if not force_refresh and prior is not None and prior.status in (
            LLMCallStatus.SUCCESS,
            LLMCallStatus.CACHED,
        ):
            return LLMResult(
                text=prior.response_text,
                parsed=prior.response_json or None,
                cost_usd=0.0,
                input_tokens=prior.input_tokens,
                output_tokens=prior.output_tokens,
                cached=True,
                call=prior,
            )

        self._check_budget()

        ct = ContentType.objects.get_for_model(target) if target is not None else None
        obj_id = target.pk if target is not None else None

        # Concurrency-safe upsert: another worker may race us on the same
        # input_hash. We try create() first; if the UNIQUE constraint fires
        # we re-read and reuse the row.
        if prior is not None:
            call = prior
            call.content_type = ct
            call.object_id = obj_id
            call.prompt_text = full_prompt[:8000]
            call.status = LLMCallStatus.PENDING
            call.error_text = ""
            call.import_run = import_run
            call.save(
                update_fields=[
                    "content_type",
                    "object_id",
                    "prompt_text",
                    "status",
                    "error_text",
                    "import_run",
                    "updated_at",
                ]
            )
        else:
            try:
                call = LLMCall.objects.create(
                    input_hash=input_hash,
                    provider=self.provider_name,
                    model=self.model,
                    task=task,
                    content_type=ct,
                    object_id=obj_id,
                    prompt_text=full_prompt[:8000],
                    status=LLMCallStatus.PENDING,
                    import_run=import_run,
                )
            except IntegrityError:
                # Another worker created the row between our select and
                # our insert. Re-read and (if it succeeded) hand it back
                # as a cache hit, otherwise reuse it as our pending row.
                call = LLMCall.objects.get(
                    input_hash=input_hash,
                    provider=self.provider_name,
                    model=self.model,
                    task=task,
                )
                if not force_refresh and call.status in (
                    LLMCallStatus.SUCCESS,
                    LLMCallStatus.CACHED,
                ):
                    return LLMResult(
                        text=call.response_text,
                        parsed=call.response_json or None,
                        cost_usd=0.0,
                        input_tokens=call.input_tokens,
                        output_tokens=call.output_tokens,
                        cached=True,
                        call=call,
                    )
                call.content_type = ct
                call.object_id = obj_id
                call.prompt_text = full_prompt[:8000]
                call.status = LLMCallStatus.PENDING
                call.error_text = ""
                call.import_run = import_run
                call.save(
                    update_fields=[
                        "content_type",
                        "object_id",
                        "prompt_text",
                        "status",
                        "error_text",
                        "import_run",
                        "updated_at",
                    ]
                )

        try:
            response = self.provider.chat(
                ChatRequest(
                    prompt=prompt,
                    schema=schema,
                    system_instruction=system_instruction,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                )
            )
        except Exception as exc:
            call.status = LLMCallStatus.FAILED
            call.error_text = repr(exc)[:4000]
            call.save(update_fields=["status", "error_text", "updated_at"])
            raise

        call.response_text = (response.text or "")[:16000]
        call.response_json = response.parsed if isinstance(response.parsed, (dict, list)) else {}
        call.input_tokens = response.input_tokens
        call.output_tokens = response.output_tokens
        call.cost_usd = Decimal(f"{response.cost_usd:.6f}")
        call.latency_ms = int(response.raw.get("elapsed_ms", 0) or 0)
        call.status = LLMCallStatus.SUCCESS
        call.save()

        return LLMResult(
            text=response.text,
            parsed=response.parsed,
            cost_usd=response.cost_usd,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cached=False,
            call=call,
        )

    def run_concurrent(
        self,
        items: Iterable[Any],
        prepare: Callable[[Any], dict[str, Any]],
        *,
        concurrency: int = 1,
    ) -> Iterator[tuple[Any, "LLMResult | None", "Exception | None"]]:
        """Yield ``(item, result, error)`` tuples in completion order.

        ``prepare(item)`` must return kwargs accepted by ``self.run(...)``.
        DB connections opened by worker threads are closed at the end of
        each call; otherwise Django would leak a connection per worker
        and on SQLite the file gets locked.
        """
        materialised = list(items)
        if concurrency <= 1 or len(materialised) <= 1:
            for item in materialised:
                try:
                    result = self.run(**prepare(item))
                    yield item, result, None
                except Exception as exc:
                    yield item, None, exc
            return

        def worker(item):
            try:
                result = self.run(**prepare(item))
                return item, result, None
            except Exception as exc:
                return item, None, exc
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(worker, item) for item in materialised]
            for future in as_completed(futures):
                yield future.result()


__all__ = [
    "LLMService",
    "LLMResult",
    "LLMBudgetExceeded",
    "LLMProvider",
    "LLMTask",
]
