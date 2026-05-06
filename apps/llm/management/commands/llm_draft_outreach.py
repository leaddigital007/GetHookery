"""
For every Person in scope, draft personalised outreach copy via LLM
and stash three variants (short DM, long DM, cold email) on the
Person record. We do not send anything - drafts are saved into
`Person.outreach_text` with a marker so a human can review, copy
and send via the appropriate channel.

Idempotent: re-running with the same prompt + Person yields the
cached LLMCall, so cost is paid only once unless --force-refresh.

Examples:
    # Dry run on Tier S persons.
    python manage.py llm_draft_outreach --tiers S --dry-run

    # Real run on Tier S+1 persons in parallel.
    python manage.py llm_draft_outreach --tiers S,1 --apply --concurrency 8

    # Only persons with a primary-contact tag.
    python manage.py llm_draft_outreach --primary-only --apply
"""
from __future__ import annotations

import time
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.ingest.services import ingest_run
from apps.investors.models import Person
from apps.llm.models import LLMTask
from apps.llm.prompts import (
    DRAFT_OUTREACH_SCHEMA,
    DRAFT_OUTREACH_SYSTEM,
    build_draft_outreach_prompt,
)
from apps.llm.service import LLMBudgetExceeded, LLMService

DEFAULT_CONCURRENCY = 4

DRAFT_MARKER = "=== LLM DRAFT (do not send as-is) ==="
DRAFT_END_MARKER = "=== /LLM DRAFT ==="


def _build_draft_text(payload: dict, *, confidence: str) -> str:
    """Render the draft block we paste into Person.outreach_text."""
    subject = (payload.get("subject_line") or "").strip()
    dm_short = (payload.get("dm_short") or "").strip()
    dm_long = (payload.get("dm_long") or "").strip()
    email_body = (payload.get("email_body") or "").strip()
    hook = (payload.get("personalised_hook") or "").strip()
    parts = [
        DRAFT_MARKER,
        f"confidence: {confidence}",
        f"hook: {hook or '(none)'}",
        "",
        "--- X / Twitter DM (<=270 chars) ---",
        dm_short or "(empty)",
        "",
        "--- LinkedIn DM (~600-1100 chars) ---",
        dm_long or "(empty)",
        "",
        "--- Email ---",
        f"Subject: {subject}",
        "",
        email_body or "(empty)",
        "",
        DRAFT_END_MARKER,
    ]
    return "\n".join(parts)


def _replace_draft_block(existing: str, new_block: str) -> str:
    """Swap any prior LLM draft block, keep manual notes."""
    text = existing or ""
    if DRAFT_MARKER in text and DRAFT_END_MARKER in text:
        before, _, rest = text.partition(DRAFT_MARKER)
        _, _, after = rest.partition(DRAFT_END_MARKER)
        return (before.rstrip() + "\n\n" + new_block + "\n" + after.lstrip()).strip()
    sep = "\n\n" if text.strip() else ""
    return (text.rstrip() + sep + new_block).strip()


class Command(BaseCommand):
    help = "Draft personalised outreach copy per Person via LLM."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--tiers",
            type=str,
            default=None,
            help="Comma-separated fund tiers. Defaults to all.",
        )
        parser.add_argument(
            "--primary-only",
            action="store_true",
            help=(
                "Only draft for persons flagged [PRIMARY] by "
                "llm_extract_partners."
            ),
        )
        parser.add_argument(
            "--missing-only",
            action="store_true",
            help="Skip persons whose outreach_text already has a draft.",
        )
        parser.add_argument(
            "--min-confidence",
            choices=["low", "medium", "high"],
            default="low",
            help=(
                "When --apply is set, only persist drafts at or above "
                "this confidence threshold. Default: low (persist all)."
            ),
        )
        parser.add_argument("--quiet", action="store_true")
        parser.add_argument(
            "--concurrency",
            type=int,
            default=DEFAULT_CONCURRENCY,
        )

    def handle(self, *args, **options):
        limit = options.get("limit")
        dry_run = options.get("dry_run", False) or not options.get("apply", False)
        tiers_arg = options.get("tiers")
        primary_only = options.get("primary_only", False)
        missing_only = options.get("missing_only", False)
        min_conf = options.get("min_confidence") or "low"
        quiet = options.get("quiet", False)
        concurrency = max(1, int(options.get("concurrency") or 1))

        conf_rank = {"low": 0, "medium": 1, "high": 2}
        min_conf_rank = conf_rank[min_conf]

        service = LLMService()

        qs = Person.objects.select_related("fund").all()
        if tiers_arg:
            wanted = [t.strip() for t in tiers_arg.split(",") if t.strip()]
            qs = qs.filter(fund__tier__in=wanted)
        if primary_only:
            qs = qs.filter(internal_notes__contains="[PRIMARY]")
        if missing_only:
            qs = qs.exclude(outreach_text__contains=DRAFT_MARKER)
        qs = qs.filter(full_name__gt="").order_by(
            "fund__tier", "-fund__check_max_usd", "fund__name", "full_name"
        )
        if limit:
            qs = qs[:limit]

        persons = list(qs)
        with ingest_run(
            source="llm_draft_outreach",
            command="llm_draft_outreach",
            args={
                "limit": limit,
                "dry_run": dry_run,
                "tiers": tiers_arg,
                "primary_only": primary_only,
                "missing_only": missing_only,
                "min_confidence": min_conf,
                "concurrency": concurrency,
            },
        ) as run:
            run.log(
                f"Drafting outreach for {len(persons)} persons "
                f"(dry_run={dry_run}, concurrency={concurrency})."
            )

            total_cost = 0.0
            buckets = {"high": 0, "medium": 0, "low": 0}
            updates = 0
            done = 0
            started = time.monotonic()

            def _prepare(person):
                return dict(
                    task=LLMTask.DRAFT_OUTREACH,
                    prompt=build_draft_outreach_prompt(
                        person=person, fund=person.fund
                    ),
                    schema=DRAFT_OUTREACH_SCHEMA,
                    system_instruction=DRAFT_OUTREACH_SYSTEM,
                    target=person,
                    import_run=run.run,
                )

            try:
                for person, result, error in service.run_concurrent(
                    persons, _prepare, concurrency=concurrency
                ):
                    run.saw()
                    done += 1

                    if isinstance(error, LLMBudgetExceeded):
                        run.log(f"BUDGET STOP: {error}")
                        break
                    if error is not None:
                        run.failed()
                        run.log(f"  {person.full_name[:34]:34} ERROR: {error!r}")
                        continue

                    payload = result.parsed or {}
                    confidence = (payload.get("confidence") or "low").strip().lower()
                    if confidence not in conf_rank:
                        confidence = "low"
                    buckets[confidence] = buckets.get(confidence, 0) + 1
                    total_cost += result.cost_usd

                    marker = "(cached)" if result.cached else f"({result.cost_usd:.4f}$)"
                    if not quiet:
                        elapsed = time.monotonic() - started
                        rate = done / elapsed if elapsed > 0 else 0.0
                        hook = (payload.get("personalised_hook") or "")[:50]
                        self.stdout.write(
                            f"  [{done}/{len(persons)} {rate:.1f} rps] "
                            f"{(person.fund.name if person.fund else '-')[:18]:18} "
                            f"-> {person.full_name[:22]:22} {confidence:6} "
                            f"hook={hook} {marker}"
                        )

                    if dry_run:
                        continue
                    if conf_rank[confidence] < min_conf_rank:
                        continue

                    block = _build_draft_text(payload, confidence=confidence)
                    person.outreach_text = _replace_draft_block(
                        person.outreach_text, block
                    )
                    person.save(update_fields=["outreach_text", "updated_at"])
                    updates += 1
                    run.updated()
            except KeyboardInterrupt:
                run.log("Interrupted by user; partial results above are persisted.")

            run.log(
                "Done. updates={u} cost=${c:.4f} confidence={b}".format(
                    u=updates, c=total_cost, b=buckets
                )
            )

            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        "Dry run - nothing written. Re-run with --apply."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Persisted drafts for {updates} persons at total "
                        f"cost ${Decimal(total_cost):.4f}."
                    )
                )
