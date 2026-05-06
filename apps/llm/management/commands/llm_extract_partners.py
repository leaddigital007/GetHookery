"""
For every Fund (or a subset), ask the LLM to return the people a
founder would actually pitch and persist them as Person records.

Idempotent: each (fund, full_name) pair is reused on re-runs - we only
fill in fields that are still empty so we never clobber manual edits.

Examples:
    # Dry-run on Tier S, see what comes back.
    python manage.py llm_extract_partners --tiers S --dry-run

    # Real run on Tier S+1 in parallel.
    python manage.py llm_extract_partners --tiers S,1 --apply --concurrency 8

    # Only fill funds with no partners on file yet.
    python manage.py llm_extract_partners --apply --only-empty
"""
from __future__ import annotations

import time
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from apps.ingest.services import ingest_run
from apps.investors.models import Fund, Person
from apps.llm.models import LLMTask
from apps.llm.prompts import (
    EXTRACT_PARTNERS_SCHEMA,
    EXTRACT_PARTNERS_SYSTEM,
    build_extract_partners_prompt,
)
from apps.llm.service import LLMBudgetExceeded, LLMService

DEFAULT_CONCURRENCY = 4

PERSON_NOTE_PREFIX = "LLM-extract-partners:"


def _strip_at(handle: str) -> str:
    """Normalise twitter_handle: drop @, whitespace, slashes."""
    h = (handle or "").strip()
    if h.startswith("@"):
        h = h[1:]
    if "/" in h or " " in h:
        return ""
    return h[:60]


def _looks_like_url(s: str) -> bool:
    return (s or "").startswith(("http://", "https://"))


def _append_note(internal_notes: str, new_note: str) -> str:
    text = internal_notes or ""
    lines = [
        ln
        for ln in text.splitlines()
        if not ln.strip().startswith(PERSON_NOTE_PREFIX)
    ]
    lines.append(new_note)
    return "\n".join(line for line in lines if line.strip())[:4000]


class Command(BaseCommand):
    help = "Extract fund partners via LLM and persist as Person records."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--tiers",
            type=str,
            default=None,
            help="Comma-separated tiers, e.g. 'S,1'. Defaults to all.",
        )
        parser.add_argument(
            "--only-empty",
            action="store_true",
            help="Skip funds that already have at least one Person attached.",
        )
        parser.add_argument(
            "--min-confidence",
            choices=["low", "medium", "high"],
            default="medium",
            help=(
                "When --apply is set, only persist results at or above "
                "this confidence threshold. Default: medium."
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
        only_empty = options.get("only_empty", False)
        min_conf = options.get("min_confidence") or "medium"
        quiet = options.get("quiet", False)
        concurrency = max(1, int(options.get("concurrency") or 1))

        conf_rank = {"low": 0, "medium": 1, "high": 2}
        min_conf_rank = conf_rank[min_conf]

        service = LLMService()

        qs = Fund.objects.all()
        if tiers_arg:
            wanted = [t.strip() for t in tiers_arg.split(",") if t.strip()]
            qs = qs.filter(tier__in=wanted)
        if only_empty:
            qs = qs.annotate(p_count=Count("people")).filter(p_count=0)
        qs = qs.order_by("tier", "-check_max_usd", "name")
        if limit:
            qs = qs[:limit]

        funds = list(qs)
        with ingest_run(
            source="llm_extract_partners",
            command="llm_extract_partners",
            args={
                "limit": limit,
                "dry_run": dry_run,
                "tiers": tiers_arg,
                "only_empty": only_empty,
                "min_confidence": min_conf,
                "concurrency": concurrency,
            },
        ) as run:
            run.log(
                f"Extracting partners for {len(funds)} funds "
                f"(dry_run={dry_run}, concurrency={concurrency})."
            )

            total_cost = 0.0
            buckets = {"high": 0, "medium": 0, "low": 0}
            persons_created = 0
            persons_updated = 0
            funds_done = 0
            started = time.monotonic()

            def _prepare(fund):
                return dict(
                    task=LLMTask.EXTRACT_PARTNERS,
                    prompt=build_extract_partners_prompt(fund=fund),
                    schema=EXTRACT_PARTNERS_SCHEMA,
                    system_instruction=EXTRACT_PARTNERS_SYSTEM,
                    target=fund,
                    import_run=run.run,
                )

            try:
                for fund, result, error in service.run_concurrent(
                    funds, _prepare, concurrency=concurrency
                ):
                    run.saw()
                    funds_done += 1

                    if isinstance(error, LLMBudgetExceeded):
                        run.log(f"BUDGET STOP: {error}")
                        break
                    if error is not None:
                        run.failed()
                        run.log(f"  {fund.name[:36]:36} ERROR: {error!r}")
                        continue

                    payload = result.parsed or {}
                    partners = payload.get("partners") or []
                    confidence = (payload.get("confidence") or "low").strip().lower()
                    if confidence not in conf_rank:
                        confidence = "low"
                    buckets[confidence] = buckets.get(confidence, 0) + 1
                    total_cost += result.cost_usd

                    marker = "(cached)" if result.cached else f"({result.cost_usd:.4f}$)"
                    if not quiet:
                        elapsed = time.monotonic() - started
                        rate = funds_done / elapsed if elapsed > 0 else 0.0
                        names_preview = ", ".join(
                            (p.get("full_name") or "?")[:24] for p in partners[:3]
                        )
                        self.stdout.write(
                            f"  [{funds_done}/{len(funds)} {rate:.1f} rps] "
                            f"{fund.name[:30]:30} {confidence:6} "
                            f"n={len(partners)} {names_preview} {marker}"
                        )

                    if dry_run:
                        continue
                    if conf_rank[confidence] < min_conf_rank:
                        continue

                    for p in partners:
                        full_name = (p.get("full_name") or "").strip()
                        if not full_name or len(full_name) < 3:
                            continue
                        role = (p.get("role") or "").strip()[:120]
                        twitter = _strip_at(p.get("twitter_handle") or "")
                        linkedin = (p.get("linkedin_url") or "").strip()
                        if linkedin and not _looks_like_url(linkedin):
                            linkedin = ""
                        focus = (p.get("focus") or "").strip()[:200]
                        is_primary = bool(p.get("is_primary_contact"))

                        person, created = Person.objects.get_or_create(
                            fund=fund,
                            full_name=full_name,
                            defaults={
                                "role": role,
                                "twitter_handle": twitter,
                                "linkedin_url": linkedin,
                            },
                        )
                        update_fields = []
                        if not person.role and role:
                            person.role = role
                            update_fields.append("role")
                        if not person.twitter_handle and twitter:
                            person.twitter_handle = twitter
                            update_fields.append("twitter_handle")
                        if not person.linkedin_url and linkedin:
                            person.linkedin_url = linkedin
                            update_fields.append("linkedin_url")

                        primary_tag = " [PRIMARY]" if is_primary else ""
                        person.internal_notes = _append_note(
                            person.internal_notes,
                            f"{PERSON_NOTE_PREFIX} confidence={confidence} "
                            f"focus={focus or '-'}{primary_tag}",
                        )
                        update_fields.append("internal_notes")
                        update_fields.append("updated_at")

                        if created:
                            persons_created += 1
                            run.created()
                        else:
                            persons_updated += 1
                            run.updated()
                        person.save(update_fields=list(set(update_fields)))
            except KeyboardInterrupt:
                run.log("Interrupted by user; partial results above are persisted.")

            run.log(
                "Done. funds={f} persons_created={c} persons_updated={u} "
                "cost=${cost:.4f} confidence={b}".format(
                    f=funds_done,
                    c=persons_created,
                    u=persons_updated,
                    cost=total_cost,
                    b=buckets,
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
                        f"Created {persons_created} new Person records, "
                        f"updated {persons_updated} existing, at total cost "
                        f"${Decimal(total_cost):.4f}."
                    )
                )
