"""
Tiny end-to-end test of the configured LLM provider.

Run this once locally (and once on Heroku) to confirm that credentials,
project-id and the model name are all wired up correctly. Costs a few
cents at most because the prompt is one sentence.

    python manage.py llm_smoke_test
    python manage.py llm_smoke_test --prompt "List 3 great seed funds in EU"
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.llm.models import LLMTask
from apps.llm.service import LLMService


SMOKE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "model_used": {"type": "string"},
    },
    "required": ["answer"],
}


class Command(BaseCommand):
    help = "One-shot smoke test for the configured LLM provider."

    def add_arguments(self, parser):
        parser.add_argument(
            "--prompt",
            default="Reply with a one-sentence summary of why generative video is hot in 2026.",
        )
        parser.add_argument(
            "--force-refresh",
            action="store_true",
            help="Skip cache so we exercise a real network call.",
        )

    def handle(self, *args, **options):
        service = LLMService()
        self.stdout.write(
            f"Provider: {service.provider_name}, model: {service.model}, "
            f"budget: ${service.daily_budget_usd:.2f}/24h"
        )

        result = service.run(
            task=LLMTask.OTHER,
            prompt=options["prompt"],
            schema=SMOKE_SCHEMA,
            system_instruction="Return only valid JSON conforming to the schema.",
            force_refresh=options.get("force_refresh", False),
        )

        self.stdout.write("--- response ---")
        self.stdout.write(result.text or "(empty)")
        self.stdout.write("--- meta ---")
        self.stdout.write(
            f"cached={result.cached}  "
            f"input_tokens={result.input_tokens}  "
            f"output_tokens={result.output_tokens}  "
            f"cost=${result.cost_usd:.6f}"
        )
        self.stdout.write(self.style.SUCCESS("LLM smoke test OK."))
