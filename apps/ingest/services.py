"""Shared helpers used by every ingestion pipeline."""
from __future__ import annotations

import logging
import traceback
from contextlib import contextmanager
from typing import Any, Iterator

from django.contrib.contenttypes.models import ContentType
from django.db.models import Model
from django.utils import timezone

from .models import ExternalRef, ImportRun, ImportRunStatus

logger = logging.getLogger(__name__)


class RunHandle:
    """Counters + log accumulator passed to the body of `ingest_run`."""

    def __init__(self, run: ImportRun, log_chunks: list[str]) -> None:
        self.run = run
        self._log = log_chunks

    def log(self, message: str) -> None:
        self._log.append(message)
        logger.info(message)
        print(message, flush=True)

    def saw(self, n: int = 1) -> None:
        self.run.rows_seen = (self.run.rows_seen or 0) + n

    def created(self, n: int = 1) -> None:
        self.run.rows_created += n

    def updated(self, n: int = 1) -> None:
        self.run.rows_updated += n

    def skipped(self, n: int = 1) -> None:
        self.run.rows_skipped += n

    def failed(self, n: int = 1) -> None:
        self.run.rows_failed += n

    def flush_counters(self) -> None:
        """Persist counter changes mid-run so admin shows live progress."""
        self.run.save(
            update_fields=[
                "rows_seen",
                "rows_created",
                "rows_updated",
                "rows_skipped",
                "rows_failed",
                "updated_at",
            ]
        )


@contextmanager
def ingest_run(
    *,
    source: str,
    command: str = "",
    args: dict[str, Any] | None = None,
) -> Iterator[RunHandle]:
    """Wrap an ingestion job with consistent ImportRun bookkeeping."""
    run = ImportRun.objects.create(source=source, command=command, args=args or {})
    log_chunks: list[str] = []
    handle = RunHandle(run, log_chunks)
    try:
        yield handle
    except Exception as exc:
        log_chunks.append(f"FATAL: {exc!r}\n{traceback.format_exc()}")
        run.rows_failed = (run.rows_failed or 0) + 1
        run.save(update_fields=["rows_failed", "updated_at"])
        run.mark_done(status=ImportRunStatus.FAILED, log_tail="\n".join(log_chunks))
        raise
    else:
        handle.flush_counters()
        status = (
            ImportRunStatus.PARTIAL
            if run.rows_failed
            else ImportRunStatus.SUCCESS
        )
        run.mark_done(status=status, log_tail="\n".join(log_chunks))


def upsert_external_ref(
    *,
    source: str,
    external_id: str,
    target: Model,
    payload: dict[str, Any] | None = None,
) -> tuple[ExternalRef, bool]:
    """Upsert an ExternalRef for `target` and bump last_seen_at."""
    ct = ContentType.objects.get_for_model(target)
    ref, created = ExternalRef.objects.update_or_create(
        source=source,
        external_id=external_id,
        defaults={
            "content_type": ct,
            "object_id": target.pk,
            "last_seen_at": timezone.now(),
            "payload": payload or {},
        },
    )
    return ref, created


def find_target_by_external(
    *,
    source: str,
    external_id: str,
    model: type[Model],
) -> Model | None:
    """Return an existing internal record for `(source, external_id)`, or None."""
    ct = ContentType.objects.get_for_model(model)
    try:
        ref = ExternalRef.objects.get(
            source=source, external_id=external_id, content_type=ct
        )
    except ExternalRef.DoesNotExist:
        return None
    return ref.target


def external_ref_exists(*, source: str, external_id: str) -> bool:
    """True if any ExternalRef row exists for `(source, external_id)`, regardless of model."""
    return ExternalRef.objects.filter(source=source, external_id=external_id).exists()
