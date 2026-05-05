"""
One-off cleanup: collapse country code variants in `Fund.hq_country`
to canonical labels so the admin filter shows a single bucket.

Idempotent: re-running it does no harm.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.investors.models import Fund

CANONICAL: dict[str, str] = {
    "US": "USA",
    "UNITED STATES": "USA",
    "UNITED STATES OF AMERICA": "USA",
    "U.S.": "USA",
    "U.S.A.": "USA",
    "AMERICA": "USA",
    "GB": "United Kingdom",
    "UK": "United Kingdom",
    "GREAT BRITAIN": "United Kingdom",
    "ENGLAND": "United Kingdom",
    "UNITED KINGDOM": "United Kingdom",
    "NL": "Netherlands",
    "DE": "Germany",
    "FR": "France",
    "ES": "Spain",
    "IT": "Italy",
    "CH": "Switzerland",
    "AT": "Austria",
    "BE": "Belgium",
    "SE": "Sweden",
    "NO": "Norway",
    "DK": "Denmark",
    "FI": "Finland",
    "IE": "Ireland",
    "PL": "Poland",
    "PT": "Portugal",
    "IL": "Israel",
    "AE": "UAE",
    "UNITED ARAB EMIRATES": "UAE",
    "IN": "India",
    "SG": "Singapore",
    "AU": "Australia",
    "CA": "Canada",
    "BR": "Brazil",
}


class Command(BaseCommand):
    help = "Normalise Fund.hq_country to canonical labels."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report planned updates without writing.",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        total = 0
        for raw, canonical in CANONICAL.items():
            qs = Fund.objects.filter(hq_country__iexact=raw).exclude(
                hq_country=canonical
            )
            n = qs.count()
            if not n:
                continue
            self.stdout.write(f"  {raw!r:>30} -> {canonical!r}  ({n} funds)")
            if not dry_run:
                qs.update(hq_country=canonical)
            total += n

        verb = "planned" if dry_run else "applied"
        self.stdout.write(
            self.style.SUCCESS(f"Done. {total} country normalisations {verb}.")
        )
