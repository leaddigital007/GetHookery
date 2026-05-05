"""
Ingestion-side models for Phase 2.

These three tables sit alongside (not inside) the investors app so that
the canonical CRM tables stay clean and additive ingestion machinery can
evolve without touching them:

  - ExternalRef : (source, external_id) -> any internal record. Powers
                  idempotent upserts across pipelines.
  - ImportRun   : audit row written by every scheduled job / management
                  command, with row counters and status.
  - Signal      : triage queue. Anything we cannot confidently auto-match
                  lands here for a human to review and promote.
"""
from __future__ import annotations

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ExternalRef(TimestampedModel):
    """Maps an external identifier to an internal record for idempotency.

    Example: ('edgar', '0001834212') -> Fund(id=42).
    """

    source = models.CharField(max_length=40)
    external_id = models.CharField(max_length=255)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    last_seen_at = models.DateTimeField(default=timezone.now)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "external_id"], name="uniq_external_ref"
            ),
        ]
        indexes = [
            models.Index(fields=["source", "external_id"]),
            models.Index(fields=["content_type", "object_id"]),
        ]
        ordering = ["-last_seen_at"]

    def __str__(self) -> str:
        return f"{self.source}:{self.external_id} -> {self.target}"


class ImportRunStatus(models.TextChoices):
    RUNNING = "running", "Running"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    PARTIAL = "partial", "Partial"


class ImportRun(TimestampedModel):
    """Audit row for every pipeline execution."""

    source = models.CharField(max_length=40, db_index=True)
    command = models.CharField(max_length=120, blank=True)
    args = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=12, choices=ImportRunStatus.choices, default=ImportRunStatus.RUNNING
    )
    rows_seen = models.PositiveIntegerField(default=0)
    rows_created = models.PositiveIntegerField(default=0)
    rows_updated = models.PositiveIntegerField(default=0)
    rows_skipped = models.PositiveIntegerField(default=0)
    rows_failed = models.PositiveIntegerField(default=0)
    log = models.TextField(
        blank=True, help_text="Tail of stdout / errors for quick review in admin"
    )

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["source", "-started_at"]),
            models.Index(fields=["status"]),
        ]

    def mark_done(
        self,
        *,
        status: str = ImportRunStatus.SUCCESS,
        log_tail: str | None = None,
    ) -> None:
        self.finished_at = timezone.now()
        self.status = status
        if log_tail is not None:
            self.log = log_tail[-4000:]
        self.save(
            update_fields=["finished_at", "status", "log", "updated_at"]
        )

    @property
    def duration_seconds(self) -> int | None:
        if not self.finished_at:
            return None
        return int((self.finished_at - self.started_at).total_seconds())

    def __str__(self) -> str:
        return f"{self.source} @ {self.started_at:%Y-%m-%d %H:%M} [{self.status}]"


class SignalKind(models.TextChoices):
    UNMATCHED_FILER = "unmatched_filer", "Unmatched filer / fund"
    NEW_DEAL_HINT = "new_deal_hint", "Possible new deal"
    PARTNER_POST = "partner_post", "Partner social post"
    NEW_PORTFOLIO_HIT = "new_portfolio_hit", "Portfolio company hit"
    OTHER = "other", "Other"


class SignalStatus(models.TextChoices):
    NEW = "new", "New"
    REVIEWING = "reviewing", "Reviewing"
    PROMOTED = "promoted", "Promoted"
    IGNORED = "ignored", "Ignored"


class Signal(TimestampedModel):
    """Triage queue: raw observations that may or may not match our records."""

    source = models.CharField(max_length=40, db_index=True)
    kind = models.CharField(max_length=32, choices=SignalKind.choices)
    summary = models.CharField(
        max_length=500, help_text="Human-readable one-liner for the admin list view"
    )
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw data from the source (filer name, RSS entry, tweet body...)",
    )
    fired_at = models.DateTimeField(
        default=timezone.now, help_text="When the source observed the signal"
    )
    status = models.CharField(
        max_length=12, choices=SignalStatus.choices, default=SignalStatus.NEW
    )
    suggested_fund = models.ForeignKey(
        "investors.Fund",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="suggested_signals",
    )
    suggested_person = models.ForeignKey(
        "investors.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="suggested_signals",
    )
    suggested_company = models.ForeignKey(
        "investors.Company",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="suggested_signals",
    )
    operator_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-fired_at"]
        indexes = [
            models.Index(fields=["status", "-fired_at"]),
            models.Index(fields=["kind", "status"]),
        ]

    def __str__(self) -> str:
        return f"[{self.get_kind_display()}] {self.summary[:80]}"
