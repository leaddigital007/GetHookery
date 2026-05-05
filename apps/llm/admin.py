"""Read-mostly admin for LLM audit rows."""
from __future__ import annotations

from django.contrib import admin

from .models import LLMCall


@admin.register(LLMCall)
class LLMCallAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "task",
        "provider",
        "model",
        "status",
        "target",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "latency_ms",
    )
    list_filter = ("provider", "model", "task", "status")
    search_fields = ("input_hash", "prompt_text", "response_text", "error_text")
    readonly_fields = (
        "input_hash",
        "provider",
        "model",
        "task",
        "content_type",
        "object_id",
        "target",
        "prompt_text",
        "response_json",
        "response_text",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "latency_ms",
        "status",
        "error_text",
        "import_run",
        "created_at",
        "updated_at",
    )
    list_select_related = ("content_type", "import_run")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False
