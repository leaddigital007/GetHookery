"""
Auto-categorise existing Companies via LLM.

Where `llm_extract_comparables` only categorises companies it just
created from a name list, this command walks the full Company table
and assigns Tag(kind=CATEGORY) labels based on each row's name,
description and HQ. It also opportunistically updates
`is_kubricon_competitor` and `relevance_to_kubricon` when the model
sees a clear competitor.

Examples:
    python manage.py llm_categorize_companies --limit 5 --dry-run
    python manage.py llm_categorize_companies --apply
    python manage.py llm_categorize_companies --apply --only-untagged
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from apps.ingest.services import ingest_run
from apps.investors.models import Company, Tag, TagKind
from apps.llm.models import LLMTask
from apps.llm.prompts import (
    CATEGORIZE_COMPANY_SCHEMA,
    CATEGORIZE_COMPANY_SYSTEM,
    KNOWN_COMPANY_TAG_SLUGS,
    build_categorize_company_prompt,
)
from apps.llm.service import LLMBudgetExceeded, LLMService

DEFAULT_CONCURRENCY = 4


class Command(BaseCommand):
    help = "Auto-categorise existing Companies via LLM (multi-label, strict slug enum)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--only-untagged",
            action="store_true",
            help="Skip companies that already have at least one category tag.",
        )
        parser.add_argument("--quiet", action="store_true")
        parser.add_argument(
            "--concurrency",
            type=int,
            default=DEFAULT_CONCURRENCY,
            help=f"Parallel LLM workers (default {DEFAULT_CONCURRENCY}).",
        )

    def handle(self, *args, **options):
        limit = options.get("limit")
        dry_run = options.get("dry_run", False) or not options.get("apply", False)
        only_untagged = options.get("only_untagged", False)
        quiet = options.get("quiet", False)
        concurrency = max(1, int(options.get("concurrency") or 1))

        tags_by_slug: dict[str, Tag] = {
            t.slug: t for t in Tag.objects.filter(kind=TagKind.CATEGORY)
        }
        missing = [s for s in KNOWN_COMPANY_TAG_SLUGS if s not in tags_by_slug]
        if missing:
            self.stdout.write(
                self.style.WARNING(
                    f"Missing category tags: {missing}. "
                    "Run `python manage.py seed_tags` first."
                )
            )

        service = LLMService()

        qs = Company.objects.all()
        if only_untagged:
            qs = qs.filter(category_tags__isnull=True).distinct()
        qs = qs.order_by("name")
        if limit:
            qs = qs[:limit]

        companies = list(qs)
        with ingest_run(
            source="llm_categorize_companies",
            command="llm_categorize_companies",
            args={
                "limit": limit,
                "dry_run": dry_run,
                "only_untagged": only_untagged,
                "concurrency": concurrency,
            },
        ) as run:
            run.log(
                f"Categorising {len(companies)} companies "
                f"(dry_run={dry_run}, concurrency={concurrency})."
            )

            total_cost = 0.0
            companies_touched = 0
            tag_assignments = 0
            competitor_promoted = 0
            done = 0
            started = time.monotonic()

            def _prepare(company):
                return dict(
                    task=LLMTask.CATEGORIZE_COMPANY,
                    prompt=build_categorize_company_prompt(company=company),
                    schema=CATEGORIZE_COMPANY_SCHEMA,
                    system_instruction=CATEGORIZE_COMPANY_SYSTEM,
                    target=company,
                    import_run=run.run,
                )

            try:
                for company, result, error in service.run_concurrent(
                    companies, _prepare, concurrency=concurrency
                ):
                    run.saw()
                    done += 1

                    if isinstance(error, LLMBudgetExceeded):
                        run.log(f"BUDGET STOP: {error}")
                        break
                    if error is not None:
                        run.failed()
                        run.log(f"  {company.name[:40]:40} ERROR: {error!r}")
                        continue

                    payload = result.parsed or {}
                    emitted = payload.get("category_tags") or []
                    slugs = [s for s in emitted if s in tags_by_slug]
                    is_competitor = bool(payload.get("is_kubricon_competitor", False))
                    relevance = (payload.get("relevance_to_kubricon") or "").strip()
                    total_cost += result.cost_usd

                    elapsed = time.monotonic() - started
                    rate = done / elapsed if elapsed > 0 else 0.0
                    if not quiet:
                        marker = "*" if is_competitor else " "
                        self.stdout.write(
                            f" {marker}[{done}/{len(companies)} {rate:.1f} rps] "
                            f"{company.name[:34]:34} -> {slugs or '(none)'}"
                        )

                    if dry_run:
                        continue

                    existing = set(
                        company.category_tags.values_list("slug", flat=True)
                    )
                    fresh = [tags_by_slug[s] for s in slugs if s not in existing]
                    update_fields: list[str] = []
                    if fresh:
                        company.category_tags.add(*fresh)
                        tag_assignments += len(fresh)
                    if is_competitor and not company.is_kubricon_competitor:
                        company.is_kubricon_competitor = True
                        update_fields.append("is_kubricon_competitor")
                        competitor_promoted += 1
                    if relevance and not company.relevance_to_kubricon:
                        company.relevance_to_kubricon = relevance[:4000]
                        update_fields.append("relevance_to_kubricon")
                    if update_fields:
                        update_fields.append("updated_at")
                        company.save(update_fields=update_fields)
                    if fresh or update_fields:
                        companies_touched += 1
                        run.updated()
            except KeyboardInterrupt:
                run.log("Interrupted by user; partial results above are persisted.")

            run.log(
                f"Done. companies_touched={companies_touched} "
                f"new_tags={tag_assignments} "
                f"competitor_promoted={competitor_promoted} "
                f"cost=${total_cost:.4f}"
            )
            if dry_run:
                self.stdout.write(
                    self.style.WARNING("Dry run - no tags written. Re-run with --apply.")
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Categorised {companies_touched} companies with "
                        f"{tag_assignments} new tag assignments, "
                        f"{competitor_promoted} flagged as Kubricon competitor. "
                        f"Cost ${total_cost:.4f}."
                    )
                )
