"""
Import a CSV export of investor contacts (NFX Signal style).

Expected columns:
    Firm Name, Investor Full Name, Signal Profile link, Sweet Spot,
    Min, Max, Locations, Intro Source, Intro Strength

Each row produces one Person attached to a Fund (creating the Fund if
necessary). Phase 1 of the NFX import: zero network calls. Phase 2
(scrape_nfx_signal) walks the Signal Profile links to enrich Twitter,
role, and recent investments.

Examples:
    python manage.py import_nfx_csv --csv data/nfx-target-investors.csv --dry-run
    python manage.py import_nfx_csv --csv data/nfx-target-investors.csv --apply
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from apps.ingest.services import ingest_run
from apps.investors.models import Fund, FundSource, Person, PipelineStage

US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "district of columbia", "dc", "florida",
    "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
    "kentucky", "louisiana", "maine", "maryland", "massachusetts",
    "michigan", "minnesota", "mississippi", "missouri", "montana",
    "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
    "new york", "north carolina", "north dakota", "ohio", "oklahoma",
    "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
}

CANADA_PROVINCES = {
    "ontario", "quebec", "british columbia", "alberta", "manitoba",
    "saskatchewan", "nova scotia", "new brunswick", "newfoundland and labrador",
    "prince edward island",
}

REGION_KEYWORDS = {
    "san francisco bay area",
    "silicon valley",
    "united states", "usa", "canada", "europe", "asia", "africa",
    "south america", "north america", "oceania",
}

MONEY_RE = re.compile(r"\$?\s*([0-9][0-9,]*)\s*$")


def _parse_money(value: str) -> int | None:
    if value is None:
        return None
    value = value.strip().replace(" ", "").replace("$", "").replace(",", "")
    if not value or value.lower() in {"n/a", "na", "unknown"}:
        return None
    try:
        n = int(value)
    except ValueError:
        return None
    return n if n > 0 else None


def _split_locations(blob: str) -> list[str]:
    """Split a 'City, State, City, State, ...' blob into clean tokens.

    NFX Signal stores location lists comma-separated which is ambiguous
    because US locations also use "City, State" with a comma. We use the
    pair-walking heuristic: try to detect US states / Canadian provinces
    and merge them with the preceding city.
    """
    if not blob:
        return []
    parts = [p.strip() for p in blob.split(",") if p.strip()]
    out: list[str] = []
    i = 0
    while i < len(parts):
        token = parts[i]
        next_token = parts[i + 1] if i + 1 < len(parts) else ""
        nlower = next_token.lower()
        if nlower in US_STATES or nlower in CANADA_PROVINCES:
            out.append(f"{token}, {next_token}")
            i += 2
            continue
        out.append(token)
        i += 1
    return out


def _first_city_country(locations: list[str]) -> tuple[str, str]:
    """Return (city, country) using the first non-region location."""
    if not locations:
        return "", ""
    for loc in locations:
        l_lower = loc.lower()
        if l_lower in REGION_KEYWORDS:
            continue
        if "," in loc:
            head, tail = [s.strip() for s in loc.split(",", 1)]
        else:
            head, tail = loc.strip(), ""
        tail_l = tail.lower()
        if tail_l in US_STATES:
            return head, "USA"
        if tail_l in CANADA_PROVINCES:
            return head, "Canada"
        if tail:
            return head, tail
        return head, ""
    head = locations[0]
    return head, ""


def _open_csv(path_arg: str):
    if path_arg in {"-", "stdin"}:
        return sys.stdin
    p = Path(path_arg)
    if not p.exists():
        raise CommandError(f"CSV not found: {p}")
    return p.open("r", encoding="utf-8-sig", newline="")


class Command(BaseCommand):
    help = "Import a NFX Signal target-investors CSV into Fund + Person."

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            required=True,
            help="Path to CSV file. Use '-' to read from stdin.",
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--quiet", action="store_true")

    def handle(self, *args, **options):
        csv_path = options["csv"]
        dry_run = options.get("dry_run", False) or not options.get("apply", False)
        quiet = options.get("quiet", False)

        funds_created = 0
        funds_updated = 0
        persons_created = 0
        persons_updated = 0
        rows_skipped = 0
        rows_total = 0

        with ingest_run(
            source="nfx_signal_csv",
            command="import_nfx_csv",
            args={"csv": csv_path, "dry_run": dry_run},
        ) as run:
            stream = _open_csv(csv_path)
            try:
                reader = csv.DictReader(stream)
                for row in reader:
                    rows_total += 1
                    run.saw()
                    firm_name = (row.get("Firm Name") or "").strip()
                    full_name = (row.get("Investor Full Name") or "").strip()
                    if not full_name:
                        rows_skipped += 1
                        continue

                    profile_url = (row.get("Signal Profile link") or "").strip()
                    sweet_spot = _parse_money(row.get("Sweet Spot") or "")
                    check_min = _parse_money(row.get("Min") or "")
                    check_max = _parse_money(row.get("Max") or "")
                    locations = _split_locations(row.get("Locations") or "")
                    intro_source = (row.get("Intro Source") or "").strip()
                    intro_strength = (row.get("Intro Strength") or "").strip()

                    primary_loc = locations[0] if locations else ""
                    fund_city, fund_country = _first_city_country(locations)

                    fund: Fund | None = None
                    if firm_name:
                        slug = slugify(firm_name)[:200]
                        fund = (
                            Fund.objects.filter(slug=slug).first()
                            or Fund.objects.filter(name__iexact=firm_name).first()
                        )
                        if fund is None:
                            if not dry_run:
                                fund = Fund.objects.create(
                                    name=firm_name[:255],
                                    slug=slug or slugify(full_name)[:200],
                                    source=FundSource.SIGNAL_NFX,
                                    check_min_usd=check_min,
                                    check_max_usd=check_max,
                                    hq_city=fund_city[:120],
                                    hq_country=fund_country[:80],
                                )
                            funds_created += 1
                        else:
                            updates = {}
                            if check_min and not fund.check_min_usd:
                                updates["check_min_usd"] = check_min
                            if check_max and not fund.check_max_usd:
                                updates["check_max_usd"] = check_max
                            if fund_city and not fund.hq_city:
                                updates["hq_city"] = fund_city[:120]
                            if fund_country and not fund.hq_country:
                                updates["hq_country"] = fund_country[:80]
                            if updates and not dry_run:
                                for k, v in updates.items():
                                    setattr(fund, k, v)
                                fund.save(update_fields=list(updates.keys()) + ["updated_at"])
                            if updates:
                                funds_updated += 1

                    person = (
                        Person.objects.filter(
                            full_name__iexact=full_name, fund=fund
                        ).first()
                        if fund
                        else Person.objects.filter(
                            full_name__iexact=full_name, fund__isnull=True
                        ).first()
                    )

                    note_lines: list[str] = []
                    if profile_url:
                        note_lines.append(f"NFX Signal: {profile_url}")
                    if sweet_spot:
                        note_lines.append(f"Sweet spot: ${sweet_spot:,}")
                    if check_min or check_max:
                        lo = f"${check_min:,}" if check_min else "?"
                        hi = f"${check_max:,}" if check_max else "?"
                        note_lines.append(f"Personal check range: {lo} - {hi}")
                    if locations:
                        note_lines.append("Locations: " + " | ".join(locations[:6]))
                    if intro_source and intro_source.lower() not in {"n/a", "na"}:
                        note_lines.append(f"Intro source: {intro_source}")
                    if intro_strength:
                        note_lines.append(f"Intro strength: {intro_strength}")
                    nfx_block = "\n".join(note_lines)

                    if person is None:
                        if not dry_run:
                            person = Person.objects.create(
                                full_name=full_name[:200],
                                fund=fund,
                                location=primary_loc[:120],
                                pipeline_stage=PipelineStage.IDENTIFIED,
                                internal_notes=nfx_block,
                            )
                        persons_created += 1
                        if not quiet:
                            self.stdout.write(
                                f"+ {full_name:30} ({firm_name[:30]})"
                            )
                    else:
                        updates = {}
                        if primary_loc and not person.location:
                            updates["location"] = primary_loc[:120]
                        existing = person.internal_notes or ""
                        if "NFX Signal:" not in existing and nfx_block:
                            merged = (existing + "\n\n" + nfx_block).strip()
                            updates["internal_notes"] = merged
                        if updates and not dry_run:
                            for k, v in updates.items():
                                setattr(person, k, v)
                            person.save(
                                update_fields=list(updates.keys()) + ["updated_at"]
                            )
                        if updates:
                            persons_updated += 1
                        if not quiet and updates:
                            self.stdout.write(
                                f"~ {full_name:30} ({firm_name[:30]})"
                            )

                    run.updated()

            finally:
                if stream is not sys.stdin:
                    stream.close()

            run.log(
                f"NFX import: rows={rows_total} skipped={rows_skipped} "
                f"funds_new={funds_created} funds_updated={funds_updated} "
                f"persons_new={persons_created} persons_updated={persons_updated}"
            )

            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        f"Dry run: would create {funds_created} funds, "
                        f"{persons_created} persons; "
                        f"update {funds_updated} funds, {persons_updated} persons. "
                        "Re-run with --apply."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"NFX import done: +{funds_created} funds, "
                        f"+{persons_created} persons; "
                        f"updated {funds_updated} funds, {persons_updated} persons."
                    )
                )
