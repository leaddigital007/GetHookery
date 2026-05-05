"""
Extract comparable companies + their funding rounds via LLM.

Default target list mirrors Kubricon's competitive landscape. Output
populates Company + Deal records and creates Investments for any fund
name we already have in the DB. Unrecognised investor names are stored
on Deal.notes for manual triage so we don't fabricate fund records.

Examples:
    python manage.py llm_extract_comparables --dry-run
    python manage.py llm_extract_comparables --apply
    python manage.py llm_extract_comparables --apply --names Runway,Pika,Suno
"""
from __future__ import annotations

import datetime
import time
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils.text import slugify

from apps.ingest.services import ingest_run
from apps.investors.models import Company, Deal, Fund, Investment
from apps.llm.models import LLMTask
from apps.llm.prompts import (
    EXTRACT_COMPANY_SCHEMA,
    EXTRACT_COMPANY_SYSTEM,
    build_extract_company_prompt,
)
from apps.llm.service import LLMBudgetExceeded, LLMService

DEFAULT_CONCURRENCY = 4

DEFAULT_NAMES = [
    "Runway",
    "Pika",
    "Luma AI",
    "HeyGen",
    "Synthesia",
    "ElevenLabs",
    "Suno",
    "Captions",
    "Descript",
    "Krea AI",
]

CONTEXT_HINTS = {
    "Runway": "AI video generation; raised multiple rounds led by tier-1 US VCs.",
    "Pika": "Text-to-video startup founded 2023 in San Francisco.",
    "Luma AI": "Generative video / 3D capture; Andreessen-Horowitz portfolio.",
    "HeyGen": "AI avatars / video for marketing teams.",
    "Synthesia": "AI avatar video for enterprise; UK-based.",
    "ElevenLabs": "AI voice / text-to-speech, London-based.",
    "Suno": "AI music generation, Cambridge MA.",
    "Captions": "AI video editing for creators.",
    "Descript": "AI-driven podcast / video editor.",
    "Krea AI": "AI image / video creation tools.",
}


def _parse_iso_date(value: str) -> datetime.date | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _normalise_amount(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return max(value, 0) or None
    try:
        return max(int(Decimal(str(value))), 0) or None
    except Exception:
        return None


def _find_fund_by_name(name: str) -> Fund | None:
    if not name:
        return None
    name = name.strip()
    if not name:
        return None
    qs = Fund.objects.filter(
        Q(name__iexact=name) | Q(slug=slugify(name)[:200])
    )
    return qs.first()


class Command(BaseCommand):
    help = "Extract comparable companies + rounds via LLM (Vertex by default)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--names",
            type=str,
            default=None,
            help="Comma-separated company names. Defaults to the Kubricon comp set.",
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--quiet", action="store_true")
        parser.add_argument(
            "--concurrency",
            type=int,
            default=DEFAULT_CONCURRENCY,
            help=f"Parallel LLM workers (default {DEFAULT_CONCURRENCY}).",
        )

    def handle(self, *args, **options):
        names_arg = options.get("names")
        names = [n.strip() for n in (names_arg or "").split(",") if n.strip()] or DEFAULT_NAMES
        dry_run = options.get("dry_run", False) or not options.get("apply", False)
        quiet = options.get("quiet", False)
        concurrency = max(1, int(options.get("concurrency") or 1))

        service = LLMService()

        with ingest_run(
            source="llm_extract_comparables",
            command="llm_extract_comparables",
            args={"names": names, "dry_run": dry_run, "concurrency": concurrency},
        ) as run:
            run.log(
                f"Extracting {len(names)} companies (dry_run={dry_run}, concurrency={concurrency})."
            )
            total_cost = 0.0
            companies_touched = 0
            deals_created = 0
            investments_created = 0
            done = 0
            started = time.monotonic()

            def _prepare(name):
                return dict(
                    task=LLMTask.EXTRACT_COMPANY,
                    prompt=build_extract_company_prompt(
                        company_name=name, hint=CONTEXT_HINTS.get(name, "")
                    ),
                    schema=EXTRACT_COMPANY_SCHEMA,
                    system_instruction=EXTRACT_COMPANY_SYSTEM,
                    max_output_tokens=4096,
                    import_run=run.run,
                )

            for name, result, error in service.run_concurrent(
                names, _prepare, concurrency=concurrency
            ):
                run.saw()
                done += 1

                if isinstance(error, LLMBudgetExceeded):
                    run.log(f"BUDGET STOP: {error}")
                    break
                if error is not None:
                    run.failed()
                    run.log(f"  {name:30} ERROR: {error!r}")
                    continue

                payload = result.parsed or {}
                company_data = payload.get("company") or {}
                rounds = payload.get("rounds") or []
                total_cost += result.cost_usd

                if not quiet:
                    self.stdout.write(
                        f"  {name:25} -> {len(rounds)} rounds "
                        f"({'cached' if result.cached else f'${result.cost_usd:.4f}'})"
                    )

                if dry_run:
                    for r in rounds[:3]:
                        self.stdout.write(
                            f"    {r.get('stage','?'):12} "
                            f"${(r.get('amount_usd') or 0)/1e6:>5.1f}M  "
                            f"lead={r.get('lead_investor','?')}"
                        )
                    continue

                slug = slugify(company_data.get("name") or name)[:200]
                if not slug:
                    continue

                company, _ = Company.objects.update_or_create(
                    slug=slug,
                    defaults={
                        "name": (company_data.get("name") or name)[:255],
                        "website": (company_data.get("website") or "")[:200],
                        "description": (company_data.get("description") or "")[:4000],
                        "hq": (company_data.get("hq") or "")[:200],
                        "is_kubricon_competitor": bool(
                            company_data.get("is_kubricon_competitor", False)
                        ),
                        "relevance_to_kubricon": (
                            company_data.get("relevance_to_kubricon") or ""
                        )[:4000],
                    },
                )
                companies_touched += 1

                for r in rounds:
                    stage = (r.get("stage") or "").strip()[:40]
                    if not stage:
                        continue
                    announced = _parse_iso_date(r.get("announced_at") or "")
                    amount = _normalise_amount(r.get("amount_usd"))
                    investors = [
                        i.strip() for i in (r.get("all_investors") or []) if i and i.strip()
                    ]
                    lead = (r.get("lead_investor") or "").strip()

                    deal_qs = Deal.objects.filter(
                        company=company, stage=stage, announced_at=announced
                    )
                    deal = deal_qs.first()
                    notes_lines = []
                    unmatched_investors: list[str] = []
                    if not deal:
                        deal = Deal.objects.create(
                            company=company,
                            stage=stage,
                            amount_usd=amount,
                            announced_at=announced,
                            source_url=(r.get("source_url") or "")[:200],
                        )
                        deals_created += 1
                    else:
                        if amount and not deal.amount_usd:
                            deal.amount_usd = amount
                            deal.save(update_fields=["amount_usd", "updated_at"])

                    for investor_name in investors:
                        fund = _find_fund_by_name(investor_name)
                        if fund is None:
                            unmatched_investors.append(investor_name)
                            continue
                        _, created = Investment.objects.get_or_create(
                            fund=fund,
                            deal=deal,
                            defaults={"is_lead": lead.lower() == fund.name.lower()},
                        )
                        if created:
                            investments_created += 1

                    if unmatched_investors:
                        notes_lines.append(
                            "Unmatched investors (not in DB): "
                            + ", ".join(unmatched_investors)
                        )
                    if lead:
                        notes_lines.append(f"Lead: {lead}")
                    if notes_lines:
                        deal.notes = "\n".join(notes_lines)[:4000]
                        deal.save(update_fields=["notes", "updated_at"])

            run.log(
                f"Done. companies={companies_touched} new_deals={deals_created} "
                f"new_investments={investments_created} cost=${total_cost:.4f}"
            )
            if dry_run:
                self.stdout.write(
                    self.style.WARNING("Dry run - no Companies/Deals written.")
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Wrote {companies_touched} companies, {deals_created} new deals, "
                        f"{investments_created} new investments. Cost ${total_cost:.4f}."
                    )
                )
