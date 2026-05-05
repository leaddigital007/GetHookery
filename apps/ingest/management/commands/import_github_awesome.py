"""
Pull curated "awesome-vc"-style fund lists from GitHub and seed Fund rows.

Examples:
    # Use the bundled default list(s)
    python manage.py import_github_awesome

    # Import a single ad-hoc list (any GitHub raw markdown URL)
    python manage.py import_github_awesome \\
        --url https://raw.githubusercontent.com/foo/bar/main/README.md \\
        --source github_awesome_foo
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from apps.ingest.services import (
    find_target_by_external,
    ingest_run,
    upsert_external_ref,
)
from apps.ingest.sources.github_awesome import (
    DEFAULT_LISTS,
    AwesomeListSpec,
    fetch_markdown,
    normalize_stages,
    parse_fund_rows,
    parse_pipe_tables,
    parse_ticket_range,
)
from apps.investors.models import Fund, FundSource


class Command(BaseCommand):
    help = "Import VC funds from awesome-vc style GitHub markdown lists."

    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            help="Raw markdown URL to import. If omitted, the default lists are used.",
        )
        parser.add_argument(
            "--source",
            help="Source label for ExternalRef when --url is supplied.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report counts without writing to the database.",
        )

    def handle(self, *args, **options):
        url = options.get("url")
        source = options.get("source")
        dry_run = options.get("dry_run", False)

        if url:
            if not source:
                raise CommandError("--source is required when --url is provided")
            specs = [AwesomeListSpec(url=url, source=source)]
        else:
            specs = list(DEFAULT_LISTS)

        if not specs:
            raise CommandError("No lists to import")

        for spec in specs:
            self._import_one(spec, dry_run=dry_run)

    def _import_one(self, spec: AwesomeListSpec, *, dry_run: bool) -> None:
        with ingest_run(
            source=spec.source,
            command="import_github_awesome",
            args={"url": spec.url, "dry_run": dry_run},
        ) as run:
            run.log(f"Fetching {spec.url}")
            text = fetch_markdown(spec.url)
            tables = parse_pipe_tables(text)
            run.log(f"Found {len(tables)} pipe tables")

            all_rows = []
            for table in tables:
                all_rows.extend(parse_fund_rows(table))
            run.log(f"Found {len(all_rows)} fund-like rows")

            for fund_row in all_rows:
                run.saw()
                slug = slugify(fund_row.name)[:200] or None
                if not slug:
                    run.skipped()
                    continue

                external_id = slug

                with transaction.atomic():
                    existing = find_target_by_external(
                        source=spec.source,
                        external_id=external_id,
                        model=Fund,
                    )
                    if existing is None:
                        existing = Fund.objects.filter(slug=slug).first()

                    if existing:
                        was_updated = self._merge_into_existing(existing, fund_row, dry_run=dry_run)
                        if not dry_run:
                            upsert_external_ref(
                                source=spec.source,
                                external_id=external_id,
                                target=existing,
                                payload={
                                    "name": fund_row.name,
                                    "stage_text": fund_row.stage_text,
                                    "ticket_text": fund_row.ticket_text,
                                    "hq_text": fund_row.hq_text,
                                },
                            )
                        if was_updated:
                            run.updated()
                        else:
                            run.skipped()
                    elif dry_run:
                        run.created()
                    else:
                        check_min, check_max = parse_ticket_range(fund_row.ticket_text)
                        fund = Fund.objects.create(
                            name=fund_row.name,
                            slug=slug,
                            website=fund_row.website[:200] if fund_row.website else "",
                            hq_country=fund_row.hq_text[:80],
                            check_min_usd=check_min,
                            check_max_usd=check_max,
                            stages=normalize_stages(fund_row.stage_text),
                            portfolio_notes=fund_row.portfolio_notes,
                            source=FundSource.GITHUB_AWESOME,
                            source_url=spec.url,
                        )
                        upsert_external_ref(
                            source=spec.source,
                            external_id=external_id,
                            target=fund,
                            payload={
                                "name": fund_row.name,
                                "stage_text": fund_row.stage_text,
                                "ticket_text": fund_row.ticket_text,
                                "hq_text": fund_row.hq_text,
                            },
                        )
                        run.created()

                    # Defense in depth: even though the branches above guard
                    # writes with `if not dry_run`, roll back the per-row
                    # transaction so any future code added inside this block
                    # cannot leak data when --dry-run is set.
                    if dry_run:
                        transaction.set_rollback(True)
            run.flush_counters()
            run.log(
                f"Done {spec.source}: seen={run.run.rows_seen} "
                f"created={run.run.rows_created} updated={run.run.rows_updated} "
                f"skipped={run.run.rows_skipped}"
            )

    @staticmethod
    def _merge_into_existing(fund: Fund, row, *, dry_run: bool) -> bool:
        """Fill empty fields without overwriting existing data."""
        dirty = False
        if not fund.website and row.website:
            fund.website = row.website[:200]
            dirty = True
        if not fund.hq_country and row.hq_text:
            fund.hq_country = row.hq_text[:80]
            dirty = True
        if (fund.check_min_usd is None or fund.check_max_usd is None) and row.ticket_text:
            check_min, check_max = parse_ticket_range(row.ticket_text)
            if fund.check_min_usd is None and check_min is not None:
                fund.check_min_usd = check_min
                dirty = True
            if fund.check_max_usd is None and check_max is not None:
                fund.check_max_usd = check_max
                dirty = True
        if not fund.stages and row.stage_text:
            stages = normalize_stages(row.stage_text)
            if stages:
                fund.stages = stages
                dirty = True
        if not fund.portfolio_notes and row.portfolio_notes:
            fund.portfolio_notes = row.portfolio_notes
            dirty = True
        if dirty and not dry_run:
            fund.save()
        return dirty
