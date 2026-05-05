"""Admin pages for ingestion artifacts (read-mostly)."""
from __future__ import annotations

from django.contrib import admin, messages

from .models import (
    ExternalRef,
    ImportRun,
    ImportRunStatus,
    Signal,
    SignalStatus,
)


@admin.register(ExternalRef)
class ExternalRefAdmin(admin.ModelAdmin):
    list_display = ("source", "external_id", "target", "last_seen_at")
    list_filter = ("source",)
    search_fields = ("source", "external_id")
    readonly_fields = (
        "source",
        "external_id",
        "content_type",
        "object_id",
        "target",
        "payload",
        "last_seen_at",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "last_seen_at"
    list_select_related = ("content_type",)

    def has_add_permission(self, request):
        return False


@admin.register(ImportRun)
class ImportRunAdmin(admin.ModelAdmin):
    list_display = (
        "source",
        "command",
        "status",
        "started_at",
        "duration",
        "rows_seen",
        "rows_created",
        "rows_updated",
        "rows_skipped",
        "rows_failed",
    )
    list_filter = ("source", "status", "command")
    search_fields = ("source", "command", "log")
    readonly_fields = (
        "source",
        "command",
        "args",
        "started_at",
        "finished_at",
        "status",
        "rows_seen",
        "rows_created",
        "rows_updated",
        "rows_skipped",
        "rows_failed",
        "log",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "started_at"
    ordering = ("-started_at",)

    @admin.display(description="Duration")
    def duration(self, obj: ImportRun) -> str:
        seconds = obj.duration_seconds
        if seconds is None:
            return "-"
        if seconds < 60:
            return f"{seconds}s"
        minutes, sec = divmod(seconds, 60)
        return f"{minutes}m {sec}s"

    def has_add_permission(self, request):
        return False


@admin.action(description="Mark as Reviewing")
def signal_mark_reviewing(modeladmin, request, queryset):
    updated = queryset.update(status=SignalStatus.REVIEWING)
    modeladmin.message_user(request, f"{updated} updated", messages.SUCCESS)


@admin.action(description="Mark as Promoted")
def signal_mark_promoted(modeladmin, request, queryset):
    updated = queryset.update(status=SignalStatus.PROMOTED)
    modeladmin.message_user(request, f"{updated} updated", messages.SUCCESS)


@admin.action(description="Mark as Ignored")
def signal_mark_ignored(modeladmin, request, queryset):
    updated = queryset.update(status=SignalStatus.IGNORED)
    modeladmin.message_user(request, f"{updated} updated", messages.SUCCESS)


@admin.register(Signal)
class SignalAdmin(admin.ModelAdmin):
    list_display = (
        "fired_at",
        "kind",
        "source",
        "summary_short",
        "status",
        "suggested_fund",
        "suggested_company",
    )
    list_filter = ("status", "kind", "source")
    search_fields = (
        "summary",
        "operator_note",
        "suggested_fund__name",
        "suggested_company__name",
    )
    autocomplete_fields = (
        "suggested_fund",
        "suggested_person",
        "suggested_company",
    )
    list_select_related = (
        "suggested_fund",
        "suggested_company",
    )
    date_hierarchy = "fired_at"
    actions = (signal_mark_reviewing, signal_mark_promoted, signal_mark_ignored)
    readonly_fields = ("payload", "fired_at", "source", "kind", "summary", "created_at", "updated_at")
    fields = (
        "fired_at",
        "kind",
        "source",
        "summary",
        "status",
        "suggested_fund",
        "suggested_person",
        "suggested_company",
        "operator_note",
        "payload",
        "created_at",
        "updated_at",
    )

    @admin.display(description="Summary")
    def summary_short(self, obj: Signal) -> str:
        return obj.summary if len(obj.summary) <= 80 else obj.summary[:77] + "..."
