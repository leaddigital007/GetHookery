"""
Parse `Fund.portfolio_notes` for markdown links of the form `[Name](url)`,
upsert a `Company` row for each, and create a `PortfolioMention` linking the
two so we can see "who has this company in their portfolio?" in admin.

Idempotent: re-running does not create duplicate Companies or mentions.
"""
from __future__ import annotations

import re

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from apps.investors.models import Company, Fund, PortfolioMention

MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
SOURCE_LABEL = "github_awesome"


class Command(BaseCommand):
    help = (
        "Extract Companies from Fund.portfolio_notes (markdown links) "
        "and create PortfolioMention rows."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created without writing.",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)

        companies_created = 0
        mentions_created = 0
        funds_touched = 0
        for fund in Fund.objects.all():
            text = fund.portfolio_notes or ""
            if not text:
                continue
            matches = MD_LINK_RE.findall(text)
            if not matches:
                continue

            funds_touched += 1
            for raw_name, raw_url in matches:
                name = raw_name.strip()
                url = raw_url.strip()
                if not name:
                    continue

                slug = slugify(name)[:200]
                if not slug:
                    continue

                with transaction.atomic():
                    company, was_created = Company.objects.get_or_create(
                        slug=slug,
                        defaults={
                            "name": name,
                            "website": url[:200] if url.startswith("http") else "",
                        },
                    )
                    if was_created:
                        companies_created += 1
                    elif not company.website and url.startswith("http"):
                        company.website = url[:200]
                        company.save(update_fields=["website", "updated_at"])

                    mention, mention_created = PortfolioMention.objects.get_or_create(
                        fund=fund,
                        company=company,
                        defaults={
                            "source_url": url[:200] if url.startswith("http") else "",
                            "source_label": SOURCE_LABEL,
                            "raw_text": f"[{name}]({url})"[:300],
                        },
                    )
                    if mention_created:
                        mentions_created += 1

                    if dry_run:
                        if was_created or mention_created:
                            self.stdout.write(
                                f"  + {fund.name[:25]:25} -> {name[:35]} "
                                f"({'NEW company' if was_created else 'company'}, "
                                f"{'NEW mention' if mention_created else 'existing'})"
                            )
                        # In dry-run we still want to leave the DB clean.
                        transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Funds processed: {funds_touched}. "
                f"Companies {'would be' if dry_run else ''} created: {companies_created}. "
                f"Mentions {'would be' if dry_run else ''} created: {mentions_created}."
            )
        )
