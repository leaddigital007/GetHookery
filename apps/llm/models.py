"""
Audit + idempotency layer for every LLM call we make.

One row per (input_hash, provider, model, task) combination. The hash is
computed from the prompt text plus a salt that includes the JSON schema
so a change in expected output forces a fresh call. Cached rows are
returned without hitting the network, which keeps repeat runs fast and
prevents accidental dollar-burn.

`target` is a generic FK so the same table can audit fund-scoring,
company-extraction, person-extraction, etc. without per-task tables.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class LLMProvider(models.TextChoices):
    VERTEX = "vertex", "Vertex AI"
    OPENAI = "openai", "OpenAI"


class LLMCallStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCESS = "success", "Success"
    CACHED = "cached", "Cached"
    FAILED = "failed", "Failed"


class LLMTask(models.TextChoices):
    SCORE_FUND = "score_fund", "Score fund"
    SMART_TAG_FUND = "smart_tag_fund", "Smart-tag fund"
    EXTRACT_COMPANY = "extract_company", "Extract company"
    CATEGORIZE_COMPANY = "categorize_company", "Categorize company"
    EXTRACT_PERSON = "extract_person", "Extract person"
    EXTRACT_PARTNERS = "extract_partners", "Extract fund partners"
    FIND_SUBMISSION_FORM = "find_submission_form", "Find submission form"
    DRAFT_OUTREACH = "draft_outreach", "Draft outreach email"
    OTHER = "other", "Other"


def _stable_hash(payload: dict[str, Any]) -> str:
    """Deterministic SHA-256 of a JSON-serialisable payload."""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class LLMCall(TimestampedModel):
    """A single LLM round-trip with full audit + idempotency."""

    input_hash = models.CharField(max_length=64, db_index=True)
    provider = models.CharField(max_length=16, choices=LLMProvider.choices)
    model = models.CharField(max_length=80)
    task = models.CharField(
        max_length=32, choices=LLMTask.choices, default=LLMTask.OTHER
    )

    # Generic link to the entity we are reasoning about (Fund, Company...).
    # Optional for tasks that are not bound to a single record.
    content_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.SET_NULL
    )
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    target = GenericForeignKey("content_type", "object_id")

    prompt_text = models.TextField(blank=True)
    response_json = models.JSONField(default=dict, blank=True)
    response_text = models.TextField(blank=True)

    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    latency_ms = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=12, choices=LLMCallStatus.choices, default=LLMCallStatus.PENDING
    )
    error_text = models.TextField(blank=True)

    import_run = models.ForeignKey(
        "ingest.ImportRun",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="llm_calls",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["input_hash"]),
            models.Index(fields=["task", "-created_at"]),
            models.Index(fields=["provider", "model"]),
            models.Index(fields=["content_type", "object_id"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["input_hash", "provider", "model", "task"],
                name="uniq_llm_call_input",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.task} via {self.provider}/{self.model} [{self.status}]"

    @staticmethod
    def hash_input(
        *,
        provider: str,
        model: str,
        task: str,
        prompt_text: str,
        schema_version: str = "v1",
    ) -> str:
        """Build a deterministic input_hash for cache lookups."""
        return _stable_hash(
            {
                "provider": provider,
                "model": model,
                "task": task,
                "prompt": prompt_text,
                "schema_version": schema_version,
            }
        )
