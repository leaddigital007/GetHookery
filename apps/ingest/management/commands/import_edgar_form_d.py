"""
Pull recent SEC EDGAR Form D filings and:

  * Upsert every issuer as a Company + create a Deal for the round.
  * For "Pooled Investment Fund" issuers, route to the Signal triage queue
    as candidate Funds.
  * Emit a Signal of kind "new_deal_hint" for non-fund filings so the
    operator can later research who actually invested in that round.

Examples:
    # Default: last 1 day, up to 200 filings
    python manage.py import_edgar_form_d

    # Backfill last 30 days, up to 500 filings
    python manage.py import_edgar_form_d --days 30 --max 500

    # Filter by full-text query (e.g. AI / generative-related)
    python manage.py import_edgar_form_d --days 7 --query "artificial intelligence"
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.dateparse import parse_date
from django.utils.text import slugify

from apps.ingest.models import Signal, SignalKind, SignalStatus
from apps.ingest.services import (
    external_ref_exists,
    ingest_run,
    upsert_external_ref,
)
from apps.ingest.sources.edgar_form_d import (
    POOLED_FUND_INDUSTRY,
    default_date_window,
    derive_stage,
    fetch_filing_detail,
    search_recent_form_d,
)
from apps.investors.models import Company, Deal, FundSource

SOURCE = "edgar_form_d"


class Command(BaseCommand):
    help = "Import recent SEC EDGAR Form D filings as Companies + Deals + triage Signals."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=1,
            help="How many days back to look. Default: 1 (yesterday + today).",
        )
        parser.add_argument(
            "--max",
            type=int,
            default=200,
            help="Hard cap on number of filings processed in one run.",
        )
        parser.add_argument(
            "--query",
            help="Optional EDGAR full-text query, e.g. 'artificial intelligence'.",
        )
        parser.add_argument(
            "--include-pooled",
            action="store_true",
            help="Also create Signals for Pooled Investment Fund filings (default: yes).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse + report counts without writing.",
        )

    def handle(self, *args, **options):
        days = max(1, options["days"])
        max_results = max(1, options["max"])
        query = options.get("query") or None
        dry_run = bool(options.get("dry_run"))

        start, end = default_date_window(days_back=days)

        with ingest_run(
            source=SOURCE,
            command="import_edgar_form_d",
            args={
                "days": days,
                "max": max_results,
                "query": query,
                "dry_run": dry_run,
            },
        ) as run:
            run.log(
                f"Searching EDGAR Form D filings: {start.isoformat()}..{end.isoformat()} "
                f"max={max_results} query={query!r}"
            )
            hits = search_recent_form_d(
                start=start,
                end=end,
                query=query,
                max_results=max_results,
            )
            run.log(f"Got {len(hits)} hits from EDGAR")

            for idx, hit in enumerate(hits, start=1):
                run.saw()
                if not hit.accession:
                    run.skipped()
                    continue

                if external_ref_exists(source=SOURCE, external_id=hit.accession):
                    run.skipped()
                    continue

                try:
                    detail = fetch_filing_detail(hit)
                except Exception as exc:
                    run.log(f"  [{idx}] {hit.accession} fetch failed: {exc!r}")
                    run.failed()
                    continue

                if detail is None:
                    run.failed()
                    continue

                # Pooled investment fund → fund-side triage signal, no Company/Deal.
                if detail.industry_group == POOLED_FUND_INDUSTRY:
                    if not dry_run:
                        with transaction.atomic():
                            signal = Signal.objects.create(
                                source=SOURCE,
                                kind=SignalKind.UNMATCHED_FILER,
                                status=SignalStatus.NEW,
                                summary=(
                                    f"Pooled fund filing: {detail.issuer_name} "
                                    f"raised ${detail.total_sold_usd or 0:,} "
                                    f"on {detail.file_date}"
                                )[:500],
                                payload=detail.raw_payload | {
                                    "accession": detail.accession,
                                    "cik": detail.cik,
                                    "file_date": detail.file_date,
                                    "form": detail.form,
                                },
                            )
                            upsert_external_ref(
                                source=SOURCE,
                                external_id=detail.accession,
                                target=signal,
                                payload=detail.raw_payload,
                            )
                    run.created()
                    continue

                # Operating company → upsert Company + Deal + Signal.
                if dry_run:
                    run.created()
                    continue

                with transaction.atomic():
                    company = self._upsert_company(detail)
                    deal = self._create_deal(company, detail)
                    upsert_external_ref(
                        source=SOURCE,
                        external_id=detail.accession,
                        target=company,
                        payload=detail.raw_payload,
                    )
                    Signal.objects.create(
                        source=SOURCE,
                        kind=SignalKind.NEW_DEAL_HINT,
                        status=SignalStatus.NEW,
                        summary=(
                            f"Form D: {detail.issuer_name} "
                            f"${detail.total_sold_usd or 0:,} ({deal.stage or '?'}) "
                            f"on {detail.file_date}"
                        )[:500],
                        payload=detail.raw_payload | {
                            "accession": detail.accession,
                            "cik": detail.cik,
                            "file_date": detail.file_date,
                            "form": detail.form,
                            "stage": deal.stage,
                        },
                        suggested_company=company,
                    )
                run.created()
                if idx % 25 == 0:
                    run.flush_counters()

            run.log(
                f"Done EDGAR: seen={run.run.rows_seen} created={run.run.rows_created} "
                f"updated={run.run.rows_updated} skipped={run.run.rows_skipped} "
                f"failed={run.run.rows_failed}"
            )

    @staticmethod
    def _upsert_company(detail) -> Company:
        slug = slugify(detail.issuer_name)[:200] or f"edgar-{detail.cik}"
        defaults = {
            "name": detail.issuer_name,
            "hq": detail.state_or_country,
            "description": (
                f"Form D issuer ({detail.entity_type}) "
                f"in {detail.industry_group}/{detail.industry_subgroup}".strip(" /")
            )[:1000],
        }
        company, _ = Company.objects.get_or_create(slug=slug, defaults=defaults)
        return company

    @staticmethod
    def _create_deal(company: Company, detail) -> Deal:
        amount = detail.total_sold_usd or detail.total_offering_usd
        announced_at = parse_date(detail.file_date) if detail.file_date else None
        return Deal.objects.create(
            company=company,
            amount_usd=amount,
            stage=derive_stage(amount),
            announced_at=announced_at,
            source_url=(
                f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK="
                f"{detail.cik}&type=D&dateb=&owner=include&count=40"
            ),
            notes=(
                f"From SEC EDGAR Form D {detail.accession}\n"
                f"Industry: {detail.industry_group}/{detail.industry_subgroup}\n"
                f"Entity: {detail.entity_type}, {detail.jurisdiction}, {detail.state_or_country}"
            ),
        )
