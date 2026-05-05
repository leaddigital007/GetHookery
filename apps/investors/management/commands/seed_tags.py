"""Seed a curated set of thesis + category tags relevant to Kubricon."""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.investors.models import Tag, TagKind

THESIS_TAGS: list[tuple[str, str]] = [
    # AI core
    ("ai-foundation-models", "AI - foundation models"),
    ("ai-applied", "AI - applied / vertical"),
    ("ai-agents", "AI - agents"),
    # Generative media (Kubricon's home turf)
    ("generative-video", "Generative video"),
    ("generative-image", "Generative image"),
    ("generative-audio", "Generative audio / voice"),
    ("creator-tools", "Creator tools"),
    ("video-tooling", "Video tooling / studio"),
    # Adjacent SaaS
    ("dev-tools", "Developer tools"),
    ("infra-data", "Infrastructure / data"),
    ("b2b-saas", "B2B SaaS"),
    ("vertical-saas", "Vertical SaaS"),
    ("productivity", "Productivity"),
    ("open-source", "Open source"),
    ("consumer", "Consumer apps"),
]

CATEGORY_TAGS: list[tuple[str, str]] = [
    # Video generation / editing
    ("ai-video", "AI video"),
    ("text-to-video", "Text-to-video"),
    ("image-to-video", "Image-to-video"),
    ("motion-generation", "Motion generation"),
    ("video-editor", "Video editor"),
    ("video-clipper", "Video clipper / shorts"),
    ("video-enhancement", "Video enhancement / upscaling"),
    ("video-captioning", "Video captioning"),
    ("ai-avatar", "AI avatar / talking head"),
    ("3d-capture", "3D capture / NeRF"),
    # Adjacent media generation
    ("image-generation", "Image generation"),
    ("voice-cloning", "Voice cloning / TTS"),
    ("ai-music", "AI music generation"),
    # Platforms / distribution
    ("studio-platform", "Studio platform"),
    ("creator-platform", "Creator platform"),
    ("stock-media", "Stock media / assets"),
    ("ai-marketing", "AI marketing / ads"),
    # Adjacent infra (left from earlier seeding)
    ("infra-database", "Infra - database"),
    ("infra-observability", "Infra - observability"),
    ("dev-platform", "Dev platform"),
]


class Command(BaseCommand):
    help = "Create or update the baseline tag set used to classify funds and companies."

    @transaction.atomic
    def handle(self, *args, **options):
        created_thesis = self._upsert(THESIS_TAGS, TagKind.THESIS, "thesis")
        created_category = self._upsert(CATEGORY_TAGS, TagKind.CATEGORY, "category")
        total_created = created_thesis + created_category
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {total_created} new tags ({created_thesis} thesis, "
                f"{created_category} category)."
            )
        )

    def _upsert(self, items, kind, label) -> int:
        created = 0
        for slug, name in items:
            tag, was_created = Tag.objects.get_or_create(
                slug=slug,
                defaults={"name": name, "kind": kind},
            )
            updated = False
            if tag.kind != kind:
                tag.kind = kind
                updated = True
            if tag.name != name:
                tag.name = name
                updated = True
            if updated:
                tag.save(update_fields=["kind", "name", "updated_at"])
            if was_created:
                created += 1
                self.stdout.write(f"+ {label:8} {slug}")
        return created
