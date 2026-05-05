"""
Prompt + schema definitions for every LLM-driven task.

Keeping prompts here (and not inline inside management commands) lets us
version them, snapshot-test them and reason about cost/quality without
chasing strings across the codebase.

Kubricon thesis (v1): generative-video tooling for creators and studios.
We monetise by selling AI-native video editing / production / generation
software to professional creators and SMB media teams. Best-fit
investors back creator tools, generative AI, video infrastructure,
prosumer / SMB SaaS, and applied AI.
"""
from __future__ import annotations

KUBRICON_THESIS = (
    "Kubricon (kubricon.com) is raising a $2M Pre-seed/Seed round. "
    "We build generative-video AI tooling for professional creators and "
    "SMB media teams: AI-assisted editing, production, and generation. "
    "We compete in the same space as Runway, Pika, Luma, HeyGen, "
    "Synthesia, ElevenLabs and Suno. Ideal investors back creator tools, "
    "applied / generative AI, video infrastructure, prosumer or SMB SaaS, "
    "and back pre-seed or seed checks of $50k-$500k."
)


# Internal tag taxonomy. Keep slugs in sync with apps/investors/management/
# commands/seed_tags.py so the LLM only emits known labels.
KNOWN_FUND_TAG_SLUGS = [
    "ai-foundation-models",
    "ai-applied",
    "ai-agents",
    "generative-video",
    "generative-image",
    "generative-audio",
    "creator-tools",
    "video-tooling",
    "dev-tools",
    "infra-data",
    "b2b-saas",
    "vertical-saas",
    "productivity",
    "open-source",
    "consumer",
]


SCORE_FUND_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "tier": {
            "type": "string",
            "enum": ["S", "1", "2", "watch"],
            "description": (
                "S = direct thesis fit (generative video / creator AI / "
                "video infrastructure). "
                "1 = broad applied AI fit. "
                "2 = pre-seed friendly but loose fit. "
                "watch = no obvious fit, keep on watch list."
            ),
        },
        "relevance_score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
            "description": "0=unrelated, 100=ideal lead investor for Kubricon.",
        },
        "rationale": {
            "type": "string",
            "description": (
                "1-2 sentence justification grounded in the fund's "
                "thesis_summary, portfolio_notes, check size and stage."
            ),
        },
        "is_active": {
            "type": "boolean",
            "description": (
                "Is this fund a plausibly active VC writing checks today, "
                "based on the data provided? False if it looks dormant or "
                "is a non-fund (corporate dev arm with no recent activity)."
            ),
        },
    },
    "required": ["tier", "relevance_score", "rationale", "is_active"],
    "propertyOrdering": ["tier", "relevance_score", "rationale", "is_active"],
}


SMART_TAG_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "tags": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": KNOWN_FUND_TAG_SLUGS,
            },
            "description": (
                "Subset of internal tag slugs that apply to this fund's "
                "stated investment focus. Be strict: only emit a tag if "
                "the thesis or portfolio explicitly supports it."
            ),
        },
    },
    "required": ["tags"],
}


EXTRACT_COMPANY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "company": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "website": {"type": "string"},
                "description": {"type": "string"},
                "hq": {"type": "string"},
                "is_kubricon_competitor": {"type": "boolean"},
                "relevance_to_kubricon": {"type": "string"},
            },
            "required": ["name"],
        },
        "rounds": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "stage": {
                        "type": "string",
                        "description": "Pre-seed, Seed, Series A, B, C, ...",
                    },
                    "amount_usd": {
                        "type": "integer",
                        "description": "Round size in USD; null if unknown.",
                    },
                    "announced_at": {
                        "type": "string",
                        "description": "ISO date YYYY-MM-DD; empty if unknown.",
                    },
                    "lead_investor": {"type": "string"},
                    "all_investors": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Verified fund / firm names that participated.",
                    },
                    "source_url": {"type": "string"},
                },
                "required": ["stage"],
            },
        },
        "category_tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Free-form category labels we may map to internal tags later.",
        },
    },
    "required": ["company", "rounds"],
}


# --- Prompt builders ------------------------------------------------------

def build_score_fund_prompt(*, fund) -> str:
    """Build the scoring prompt for a single Fund record."""
    parts = [
        f"Fund name: {fund.name}",
        f"Website: {fund.website or 'unknown'}",
        f"HQ: {fund.hq_city or '?'}, {fund.hq_country or '?'}",
        f"AUM: {fund.aum_text or 'unknown'}",
        f"First-cheque min (USD): {fund.check_min_usd or 'unknown'}",
        f"First-cheque max (USD): {fund.check_max_usd or 'unknown'}",
        f"Stages: {fund.stages or 'unknown'}",
        f"Investment thesis:\n{(fund.thesis_summary or '').strip() or 'unknown'}",
        f"Portfolio notes:\n{(fund.portfolio_notes or '').strip() or 'unknown'}",
        f"Internal notes:\n{(fund.internal_notes or '').strip() or ''}",
    ]
    return "\n".join(parts)


SCORE_FUND_SYSTEM = (
    "You are a venture-capital analyst working for the founder of Kubricon. "
    "Score how good a fit a given investor is for Kubricon and return ONLY "
    "valid JSON conforming to the schema. Be conservative: only assign Tier S "
    "when the thesis or portfolio explicitly supports generative video, "
    "creator tools, or video infrastructure. Tier 1 is for broad applied-AI "
    "investors that write pre-seed/seed checks. Tier 2 is generic but "
    "pre-seed friendly. Use 'watch' for everything else.\n\n"
    f"Kubricon thesis: {KUBRICON_THESIS}"
)


SMART_TAG_SYSTEM = (
    "You are a tag classifier. Given a venture fund's thesis and portfolio "
    "notes, return ONLY the internal tag slugs that clearly apply. Be strict: "
    "no speculation. Output JSON conforming to the schema."
)


def build_smart_tag_prompt(*, fund) -> str:
    parts = [
        f"Fund name: {fund.name}",
        f"Investment thesis:\n{(fund.thesis_summary or '').strip() or 'unknown'}",
        f"Portfolio notes:\n{(fund.portfolio_notes or '').strip() or 'unknown'}",
        "",
        "Allowed tag slugs (pick zero or more):",
        ", ".join(KNOWN_FUND_TAG_SLUGS),
    ]
    return "\n".join(parts)


EXTRACT_COMPANY_SYSTEM = (
    "You are a venture-data analyst. The user will name a company in the "
    "generative-AI / creator-tools space. Return a structured snapshot of "
    "the company and its publicly-known funding rounds, with verified lead "
    "and follow-on investors. Use only your most reliable knowledge - if a "
    "round detail is unknown, leave the field empty rather than guessing. "
    "Mark is_kubricon_competitor=true only for companies that ship "
    "generative-video tools end-users actually buy."
)


def build_extract_company_prompt(*, company_name: str, hint: str = "") -> str:
    lines = [
        f"Company: {company_name}",
    ]
    if hint:
        lines.append(f"Context hint: {hint}")
    lines.append(
        "Return: company snapshot + every publicly known funding round with "
        "stage, amount in USD, announced date (YYYY-MM-DD), lead investor, "
        "all participating investors, and a source URL when you remember one."
    )
    return "\n".join(lines)
