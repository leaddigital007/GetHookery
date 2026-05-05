"""
For every Fund (or a subset), ask the LLM where founders can submit a
pitch and store the result in `Fund.submission_url` + `Fund.contact_email`.

This is a *cheap* command (~$0.005-0.02 per fund on Gemini 3 Pro) and
the output goes straight into the outreach pipeline. Run it after a
re-score so we only spend on funds that are actually in scope.

Examples:
    # Dry-run on Tier S only (preview, no DB writes).
    python manage.py llm_find_submission_forms --tiers S --dry-run

    # Real run on Tier S + 1, parallel.
    python manage.py llm_find_submission_forms --tiers S,1 --apply --concurrency 8

    # Only fill funds that don't already have a submission_url.
    python manage.py llm_find_submission_forms --apply --only-missing

    # Hard cap for safety.
    python manage.py llm_find_submission_forms --tiers S --apply --limit 50
"""
from __future__ import annotations

import time
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.ingest.services import ingest_run
from apps.investors.models import Fund
from apps.llm.models import LLMTask
from apps.llm.prompts import (
    FIND_SUBMISSION_FORM_SCHEMA,
    FIND_SUBMISSION_FORM_SYSTEM,
    build_find_submission_form_prompt,
)
from apps.llm.service import LLMBudgetExceeded, LLMService

DEFAULT_CONCURRENCY = 4

NOTE_PREFIX = "LLM-submit:"


def _replace_note(internal_notes: str, new_note: str) -> str:
    """Replace any prior LLM-submit: line, preserving everything else."""
    text = internal_notes or ""
    lines = [
        ln
        for ln in text.splitlines()
        if not ln.strip().startswith(NOTE_PREFIX)
    ]
    lines.append(new_note)
    return "\n".join(line for line in lines if line.strip())[:4000]


def _looks_like_url(s: str) -> bool:
    s = (s or "").strip()
    return s.startswith(("http://", "https://"))


class Command(BaseCommand):
    help = "Find submission URL + contact email per Fund via LLM."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--tiers",
            type=str,
            default=None,
            help=(
                "Comma-separated tiers to process, e.g. 'S,1'. "
                "Defaults to all tiers."
            ),
        )
        parser.add_argument(
            "--only-missing",
            action="store_true",
            help="Skip funds that already have a non-empty submission_url.",
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
        only_missing = options.get("only_missing", False)
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
        if only_missing:
            qs = qs.filter(Q(submission_url="") | Q(submission_url__isnull=True))
        qs = qs.order_by("tier", "-check_max_usd", "name")
        if limit:
            qs = qs[:limit]

        funds = list(qs)
        with ingest_run(
            source="llm_find_submission_forms",
            command="llm_find_submission_forms",
            args={
                "limit": limit,
                "dry_run": dry_run,
                "tiers": tiers_arg,
                "only_missing": only_missing,
                "min_confidence": min_conf,
                "concurrency": concurrency,
            },
        ) as run:
            run.log(
                f"Searching submission channels for {len(funds)} funds "
                f"(dry_run={dry_run}, concurrency={concurrency})."
            )

            total_cost = 0.0
            buckets = {"high": 0, "medium": 0, "low": 0}
            updates = 0
            done = 0
            started = time.monotonic()

            def _prepare(fund):
                return dict(
                    task=LLMTask.FIND_SUBMISSION_FORM,
                    prompt=build_find_submission_form_prompt(fund=fund),
                    schema=FIND_SUBMISSION_FORM_SCHEMA,
                    system_instruction=FIND_SUBMISSION_FORM_SYSTEM,
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
                    submission = (payload.get("submission_url") or "").strip()
                    email = (payload.get("contact_email") or "").strip()
                    alt = (payload.get("alternate_contact") or "").strip()
                    confidence = (payload.get("confidence") or "low").strip().lower()
                    if confidence not in conf_rank:
                        confidence = "low"

                    if submission and not _looks_like_url(submission):
                        submission = ""
                    buckets[confidence] = buckets.get(confidence, 0) + 1
                    total_cost += result.cost_usd

                    marker = "(cached)" if result.cached else f"({result.cost_usd:.4f}$)"
                    if not quiet:
                        elapsed = time.monotonic() - started
                        rate = done / elapsed if elapsed > 0 else 0.0
                        url_short = submission[:42] or "-"
                        self.stdout.write(
                            f"  [{done}/{len(funds)} {rate:.1f} rps] "
                            f"{fund.name[:30]:30} {confidence:6} "
                            f"url={url_short:42} email={email[:30]:30} {marker}"
                        )

                    if dry_run:
                        continue
                    if conf_rank[confidence] < min_conf_rank:
                        continue
                    if not submission and not email and not alt:
                        continue

                    note = (
                        f"{NOTE_PREFIX} confidence={confidence}"
                        + (f" url={submission}" if submission else "")
                        + (f" email={email}" if email else "")
                        + (f" alt={alt}" if alt else "")
                    )

                    update_fields = ["internal_notes", "updated_at"]
                    if submission and submission != fund.submission_url:
                        fund.submission_url = submission
                        update_fields.append("submission_url")
                    if email and email != fund.contact_email:
                        fund.contact_email = email
                        update_fields.append("contact_email")

                    fund.internal_notes = _replace_note(fund.internal_notes, note)
                    fund.save(update_fields=update_fields)
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
                        f"Updated {updates} funds at cost ${Decimal(total_cost):.4f}."
                    )
                )
