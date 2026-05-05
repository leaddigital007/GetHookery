"""
Auto-tag Funds by matching keywords against `thesis_summary`.

Same idea as `tag_funds_from_portfolio`, but it scans the OpenVC-style
short-form investor pitch instead of a list of portfolio companies.
That is the only signal we have for the ~2500 funds imported from the
OpenVC October 2025 export.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.investors.models import Fund, Tag, TagKind

# Map of thesis-tag slug -> list of substring triggers (lowercased).
# Triggers are matched against `thesis_summary` (Investment thesis on
# OpenVC). Keep them tight - false positives pollute the segmentation.
TAG_TRIGGERS: dict[str, list[str]] = {
    "ai-foundation-models": [
        "foundation model", "foundational model", "llm", "large language",
        "frontier model",
    ],
    "ai-applied": [
        "ai-native", "ai native", "applied ai", "ai application",
        "machine learning", "ml infrastructure", "mlops",
    ],
    "ai-agents": [
        "ai agent", "autonomous agent", "agentic", "ai workflow",
    ],
    "generative-video": [
        "generative video", "video generation", "ai video", "video ai",
    ],
    "generative-image": [
        "generative image", "image generation", "ai image",
    ],
    "generative-audio": [
        "generative audio", "voice ai", "audio generation",
        "text-to-speech", "voice cloning",
    ],
    "creator-tools": [
        "creator economy", "creator tool", "no-code", "no code",
        "low-code", "low code",
    ],
    "video-tooling": [
        "video editing", "video production", "video infrastructure",
        "video tooling",
    ],
    "dev-tools": [
        "developer tool", "dev tool", "devtool", "developer productivity",
        "developer experience", "developer-first",
    ],
    "infra-data": [
        "data infrastructure", "infra ", "cloud infrastructure",
        "database", "data platform", "data stack", "data lake",
        "data warehouse", "streaming data", "real-time data",
    ],
    "b2b-saas": [
        "b2b saas", "b2b software", "saas",
    ],
    "vertical-saas": [
        "vertical saas", "vertical software",
    ],
    "productivity": [
        "productivity tool", "team productivity", "workflow automation",
        "knowledge work",
    ],
    "open-source": [
        "open source", "open-source", "oss",
    ],
    "consumer": [
        "consumer app", "consumer brand", "d2c", "direct-to-consumer",
        "direct to consumer", "consumer technology",
    ],
}


class Command(BaseCommand):
    help = (
        "Auto-assign thesis tags to Funds based on keyword hits inside "
        "thesis_summary (OpenVC's 'Investment thesis' field)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report planned tag assignments without writing.",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress per-fund logs (useful with 2500+ funds).",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        quiet = options.get("quiet", False)

        tags_by_slug: dict[str, Tag] = {
            t.slug: t for t in Tag.objects.filter(kind=TagKind.THESIS)
        }

        missing = [slug for slug in TAG_TRIGGERS if slug not in tags_by_slug]
        if missing:
            self.stdout.write(
                self.style.WARNING(
                    f"Missing thesis tags: {missing}. "
                    "Run `python manage.py seed_tags` first."
                )
            )

        total_assignments = 0
        funds_touched = 0
        for fund in Fund.objects.exclude(thesis_summary="").iterator():
            text = (fund.thesis_summary or "").lower()
            if not text:
                continue

            existing = set(fund.thesis_tags.values_list("slug", flat=True))
            to_add: list[Tag] = []
            for slug, triggers in TAG_TRIGGERS.items():
                if slug in existing:
                    continue
                tag = tags_by_slug.get(slug)
                if tag is None:
                    continue
                if any(trigger in text for trigger in triggers):
                    to_add.append(tag)

            if not to_add:
                continue

            funds_touched += 1
            total_assignments += len(to_add)
            if not quiet:
                slugs = [t.slug for t in to_add]
                self.stdout.write(f"  {fund.name[:40]:40} += {slugs}")
            if not dry_run:
                fund.thesis_tags.add(*to_add)

        verb = "planned" if dry_run else "applied"
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {total_assignments} tag assignments {verb} across "
                f"{funds_touched} funds."
            )
        )
