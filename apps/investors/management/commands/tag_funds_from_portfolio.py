"""
Auto-tag Funds by matching keywords against `portfolio_notes`.

This is a heuristic but high-yield: for every fund imported from the
GitHub awesome list (or OpenVC) we already have a free-text list of their
notable bets. Mapping those bets to thesis tags lets us filter the admin
by category without manual tagging.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.investors.models import Fund, Tag, TagKind

# Map of thesis-tag slug -> list of substring triggers (case-insensitive).
# Keep triggers narrow to avoid false positives.
TAG_TRIGGERS: dict[str, list[str]] = {
    "ai-foundation-models": [
        "openai", "anthropic", "mistral", "cohere", "huggingface",
        "hugging face", "rasa", "deepset", "lightning ai",
    ],
    "ai-applied": [
        "mindsdb", "evidently", "tecton", "instill", "pachyderm",
        "weights & biases", "weights and biases", "wandb",
        "tensorzero", "phoenix.arize", "arize", "giskard", "nannyml",
    ],
    "ai-agents": [
        "adept", "fixie", "lindy", "lutra", "pickaxe", "n8n",
    ],
    "generative-video": [
        "runway", "pika", "luma", "synthesia", "heygen",
    ],
    "generative-image": [
        "stability", "midjourney", "krea", "magnific", "ideogram",
    ],
    "generative-audio": [
        "elevenlabs", "eleven labs", "suno", "udio", "play.ht",
    ],
    "creator-tools": [
        "framer", "wasp", "tooljet", "n8n", "twenty crm",
        "appwrite", "buildship",
    ],
    "video-tooling": [
        "runway", "pika", "descript", "loom", "kapwing",
    ],
    "dev-tools": [
        "docker", "gitlab", "gitpod", "posthog", "snyk", "sonarsource",
        "sourcegraph", "linear", "raycast", "shuttle", "encore",
        "darklang", "buf", "earthly", "garden.io", "novu",
    ],
    "infra-data": [
        "clickhouse", "cockroach", "redis", "mongodb", "supabase",
        "neon", "redpanda", "snowflake", "databricks", "confluent",
        "starburst", "materialize", "scylladb", "tiledb", "questdb",
        "yugabyte", "kafka", "timescale", "influxdb", "influxdata",
        "couchbase", "ferretdb", "surrealdb",
    ],
    "b2b-saas": [
        "airbyte", "metabase", "datadog", "elastic", "hubspot",
        "segment", "twilio", "stripe", "lago", "cube",
    ],
    "vertical-saas": [
        "kong", "hashicorp", "talend", "puppet", "mattermost",
    ],
    "productivity": [
        "notion", "linear", "loom", "superhuman", "twenty",
    ],
    "open-source": [
        "redhat", "red hat", "mariadb", "mysql", "hashicorp",
        "openbb", "rocket.chat", "mindsdb", "n8n", "hashicorp",
        "gitlab", "docker", "elastic", "snowplow", "clickhouse",
        "cockroach", "mongodb", "kestra", "flowforge", "fleet",
    ],
    "consumer": [
        "discord", "loom", "duolingo",
    ],
}


class Command(BaseCommand):
    help = "Auto-assign thesis tags to Funds based on keyword hits in portfolio_notes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report planned tag assignments without writing.",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
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
        for fund in Fund.objects.all():
            text = (fund.portfolio_notes or "").lower()
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
            slugs = [t.slug for t in to_add]
            self.stdout.write(f"  {fund.name[:35]:35} += {slugs}")
            if not dry_run:
                fund.thesis_tags.add(*to_add)

        verb = "planned" if dry_run else "applied"
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {total_assignments} tag assignments {verb} across "
                f"{funds_touched} funds."
            )
        )
