"""
Multi-label tag classification for Funds via LLM.

Where the keyword-based `tag_funds_from_thesis` covered 802 / 2 534 funds
(≈32%), this command lets the model reason about the thesis text and
emit any of our internal tag slugs. Strict schema -> only known slugs
get through.

Examples:
    python manage.py llm_smart_tag_funds --limit 5 --dry-run
    python manage.py llm_smart_tag_funds --limit 100 --apply
    python manage.py llm_smart_tag_funds --apply --only-untagged
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from apps.ingest.services import ingest_run
from apps.investors.models import Fund, Tag, TagKind
from apps.llm.models import LLMTask
from apps.llm.prompts import (
    KNOWN_FUND_TAG_SLUGS,
    SMART_TAG_SCHEMA,
    SMART_TAG_SYSTEM,
    build_smart_tag_prompt,
)
from apps.llm.service import LLMBudgetExceeded, LLMService

DEFAULT_CONCURRENCY = 4


class Command(BaseCommand):
    help = "Auto-tag Funds via LLM (multi-label, strict slug enum)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--only-untagged",
            action="store_true",
            help="Skip funds that already have at least one thesis tag.",
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
            t.slug: t for t in Tag.objects.filter(kind=TagKind.THESIS)
        }
        missing = [s for s in KNOWN_FUND_TAG_SLUGS if s not in tags_by_slug]
        if missing:
            self.stdout.write(
                self.style.WARNING(
                    f"Missing thesis tags: {missing}. Run `python manage.py seed_tags` first."
                )
            )

        service = LLMService()

        qs = Fund.objects.exclude(thesis_summary="")
        if only_untagged:
            qs = qs.filter(thesis_tags__isnull=True).distinct()
        qs = qs.order_by("name")
        if limit:
            qs = qs[:limit]

        funds = list(qs)
        with ingest_run(
            source="llm_smart_tag_funds",
            command="llm_smart_tag_funds",
            args={
                "limit": limit,
                "dry_run": dry_run,
                "only_untagged": only_untagged,
                "concurrency": concurrency,
            },
        ) as run:
            run.log(
                f"Tagging {len(funds)} funds (dry_run={dry_run}, concurrency={concurrency})."
            )

            total_cost = 0.0
            updates = 0
            assignments = 0
            done = 0
            started = time.monotonic()

            def _prepare(fund):
                return dict(
                    task=LLMTask.SMART_TAG_FUND,
                    prompt=build_smart_tag_prompt(fund=fund),
                    schema=SMART_TAG_SCHEMA,
                    system_instruction=SMART_TAG_SYSTEM,
                    target=fund,
                    import_run=run.run,
                )

            try:
                for fund, result, error in service.run_concurrent(
                    funds, _prepare, concurrency=concurrency
                ):
                    run.saw()
                    done += 1

                    if isinstance(error, LLMBudgetExceeded):
                        run.log(f"BUDGET STOP: {error}")
                        break
                    if error is not None:
                        run.failed()
                        run.log(f"  {fund.name[:40]:40} ERROR: {error!r}")
                        continue

                    payload = result.parsed or {}
                    emitted = payload.get("tags") or []
                    slugs = [s for s in emitted if s in tags_by_slug]
                    total_cost += result.cost_usd

                    elapsed = time.monotonic() - started
                    rate = done / elapsed if elapsed > 0 else 0.0
                    if not slugs:
                        if not quiet:
                            self.stdout.write(
                                f"  [{done}/{len(funds)} {rate:.1f} rps] "
                                f"{fund.name[:34]:34} -> (no tags)"
                            )
                        continue

                    if not quiet:
                        self.stdout.write(
                            f"  [{done}/{len(funds)} {rate:.1f} rps] "
                            f"{fund.name[:34]:34} += {slugs}"
                        )

                    if dry_run:
                        continue

                    existing = set(fund.thesis_tags.values_list("slug", flat=True))
                    fresh = [tags_by_slug[s] for s in slugs if s not in existing]
                    if not fresh:
                        continue
                    fund.thesis_tags.add(*fresh)
                    updates += 1
                    assignments += len(fresh)
                    run.updated()
            except KeyboardInterrupt:
                run.log("Interrupted by user; partial results above are persisted.")

            run.log(
                f"Done. funds_touched={updates} new_assignments={assignments} cost=${total_cost:.4f}"
            )
            if dry_run:
                self.stdout.write(
                    self.style.WARNING("Dry run - no tags written. Re-run with --apply.")
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Tagged {updates} funds with {assignments} new assignments. "
                        f"Cost ${total_cost:.4f}."
                    )
                )
