"""
Score every Fund's relevance to Kubricon using an LLM.

Outputs structured JSON via the LLMService (idempotent + audited via
LLMCall) and updates `Fund.tier` plus a one-line rationale stored in
`Fund.internal_notes` (prefixed with `LLM-score:` so it's easy to grep).

Examples:
    python manage.py llm_score_funds --limit 5 --dry-run
    python manage.py llm_score_funds --limit 50 --apply
    python manage.py llm_score_funds --apply
    python manage.py llm_score_funds --apply --only-untiered
    python manage.py llm_score_funds --apply --min-score 60
"""
from __future__ import annotations

import time
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.ingest.services import ingest_run
from apps.investors.models import Fund, FundTier
from apps.llm.models import LLMTask
from apps.llm.prompts import (
    SCORE_FUND_SCHEMA,
    SCORE_FUND_SYSTEM,
    build_score_fund_prompt,
)
from apps.llm.service import LLMBudgetExceeded, LLMService

DEFAULT_CONCURRENCY = 4

VALID_TIERS = {"S", "1", "2", "watch"}

LLM_NOTE_PREFIX = "LLM-score:"


def _replace_llm_note(internal_notes: str, new_note: str) -> str:
    """Replace any prior `LLM-score:` line in internal_notes with new_note,
    preserving everything else. Idempotent across re-runs."""
    text = internal_notes or ""
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith(LLM_NOTE_PREFIX)]
    lines.append(new_note)
    return "\n".join(line for line in lines if line.strip())[:4000]


class Command(BaseCommand):
    help = "Score Funds via LLM and assign Tier + relevance rationale."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Score at most N funds (default: all).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print results without writing tiers / notes.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write tiers and notes (default is preview only).",
        )
        parser.add_argument(
            "--only-untiered",
            action="store_true",
            help="Skip funds whose tier was already set by a prior LLM run.",
        )
        parser.add_argument(
            "--min-score",
            type=int,
            default=None,
            help=(
                "When --apply is set, only update funds whose LLM "
                "relevance_score >= this value. Useful for safe, "
                "incremental rollouts."
            ),
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Skip per-fund stdout chatter.",
        )
        parser.add_argument(
            "--concurrency",
            type=int,
            default=DEFAULT_CONCURRENCY,
            help=f"Parallel LLM workers (default {DEFAULT_CONCURRENCY}).",
        )

    def handle(self, *args, **options):
        limit = options.get("limit")
        dry_run = options.get("dry_run", False) or not options.get("apply", False)
        only_untiered = options.get("only_untiered", False)
        min_score = options.get("min_score")
        quiet = options.get("quiet", False)
        concurrency = max(1, int(options.get("concurrency") or 1))

        service = LLMService()

        qs = Fund.objects.all()
        if only_untiered:
            qs = qs.filter(
                Q(tier=FundTier.WATCH) & ~Q(internal_notes__contains=LLM_NOTE_PREFIX)
            )
        qs = qs.exclude(thesis_summary="").order_by("-check_max_usd", "name")
        if limit:
            qs = qs[:limit]

        funds = list(qs)
        with ingest_run(
            source="llm_score_funds",
            command="llm_score_funds",
            args={
                "limit": limit,
                "dry_run": dry_run,
                "only_untiered": only_untiered,
                "min_score": min_score,
                "concurrency": concurrency,
            },
        ) as run:
            run.log(
                f"Scoring {len(funds)} funds (dry_run={dry_run}, concurrency={concurrency})."
            )

            total_cost = 0.0
            tier_counts: dict[str, int] = {}
            updates = 0
            done = 0
            started = time.monotonic()

            def _prepare(fund):
                return dict(
                    task=LLMTask.SCORE_FUND,
                    prompt=build_score_fund_prompt(fund=fund),
                    schema=SCORE_FUND_SCHEMA,
                    system_instruction=SCORE_FUND_SYSTEM,
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
                    tier = (payload.get("tier") or "").strip()
                    if tier not in VALID_TIERS:
                        tier = "watch"
                    score = int(payload.get("relevance_score") or 0)
                    rationale = (payload.get("rationale") or "").strip().replace("\n", " ")
                    is_active = bool(payload.get("is_active", True))

                    tier_counts[tier] = tier_counts.get(tier, 0) + 1
                    total_cost += result.cost_usd

                    marker = "(cached)" if result.cached else f"({result.cost_usd:.4f}$)"
                    if not quiet:
                        elapsed = time.monotonic() - started
                        rate = done / elapsed if elapsed > 0 else 0.0
                        self.stdout.write(
                            f"  [{done}/{len(funds)} {rate:.1f} rps] "
                            f"{fund.name[:34]:34} -> Tier {tier:5} "
                            f"score={score:3} active={is_active} {marker}"
                        )

                    if dry_run:
                        continue
                    if min_score is not None and score < min_score:
                        continue

                    fund.tier = tier
                    fund.internal_notes = _replace_llm_note(
                        fund.internal_notes,
                        f"{LLM_NOTE_PREFIX} Tier {tier} score={score} active={is_active}. {rationale}",
                    )
                    fund.save(update_fields=["tier", "internal_notes", "updated_at"])
                    updates += 1
                    run.updated()
            except KeyboardInterrupt:
                run.log("Interrupted by user; partial results above are persisted.")

            run.log(
                "Done. updates={u} cost=${c:.4f} tiers={t}".format(
                    u=updates,
                    c=total_cost,
                    t=tier_counts,
                )
            )

            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        "Dry run - no tiers written. Re-run with --apply to persist."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Updated {updates} funds at total cost ${Decimal(total_cost):.4f}."
                    )
                )
