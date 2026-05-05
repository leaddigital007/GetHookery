"""
Prompt + schema definitions for every LLM-driven task.

Keeping prompts here (and not inline inside management commands) lets us
version them, snapshot-test them and reason about cost/quality without
chasing strings across the codebase.

Kubricon thesis (v2 - May 2026 pitch deck source of truth):
AI Creative Production for Performance Marketing teams. We sell
brand-controlled AI generation (multi-model + reference characters +
brand kits + studios) to DTC, SMM and growth teams running paid social
on Meta/TikTok/Google. The best fit investors are operator-VCs with
paid-acquisition expertise, US-focused, who back creator-economy /
DTC / SMB SaaS / applied AI. Same operating duo previously built
MyHomeQuote from $0 to $3.5M monthly revenue.
"""
from __future__ import annotations

KUBRICON_THESIS = (
    "Kubricon (kubricon.com) is raising a $1M Pre-seed on a SAFE with a "
    "$10M valuation cap (May 2026). The product is AI creative "
    "production tooling for performance-marketing teams - DTC brands, "
    "SMM/growth teams, eCommerce operators - who run paid social on "
    "Meta, TikTok and Google and need brand-consistent ad creatives at "
    "scale. The platform combines multi-model AI generation (Veo, "
    "Kling, Seedance, Flux, GPT Image) with director-level control "
    "(reference characters, products, brand kits) across four studios "
    "(Reference Studio, Marketing Studio, Storyframes, Kubricon Cinema). "
    "Differentiation vs Runway / Pika / Veo: brand control built for "
    "perf marketing teams, not generic creators. Closest comparables "
    "are Higgsfield ($200M run-rate, $1.3B val), Freepik ($230M ARR), "
    "Artlist ($300M ARR), InVideo ($70M ARR). Current state: 659 "
    "signups, 256 active users, 37.8% activation, $202 MRR, $61K total "
    "spent in 9 months. US is the #1 organic geo. Founder Igor "
    "Skobletskyi is ex-COO of MyHomeQuote (scaled $0 -> $3.5M monthly "
    "revenue) with 6 years in US performance marketing. Ideal "
    "investors are operator-VCs who themselves come from paid "
    "acquisition / growth / DTC backgrounds, are US-focused, "
    "thesis-fit on creator-economy / DTC / SMB SaaS / applied AI / "
    "AI ad creative, and write pre-seed checks of $50k-$500k."
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


# Company.category_tags taxonomy (TagKind.CATEGORY). Used by the LLM to
# classify comparable companies (Runway / Pika / Higgsfield / etc.) so we
# can group them by what they actually build and surface them in the admin.
KNOWN_COMPANY_TAG_SLUGS = [
    "ai-video",
    "text-to-video",
    "image-to-video",
    "motion-generation",
    "video-editor",
    "video-clipper",
    "video-enhancement",
    "video-captioning",
    "ai-avatar",
    "3d-capture",
    "image-generation",
    "voice-cloning",
    "ai-music",
    "studio-platform",
    "creator-platform",
    "stock-media",
    "ai-marketing",
    "infra-database",
    "infra-observability",
    "dev-platform",
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
            "items": {
                "type": "string",
                "enum": KNOWN_COMPANY_TAG_SLUGS,
            },
            "description": (
                "Subset of internal company-category slugs that apply. Be "
                "strict: only emit a slug if the company clearly ships in "
                "that category."
            ),
        },
    },
    "required": ["company", "rounds", "category_tags"],
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
    "valid JSON conforming to the schema. Be conservative.\n\n"
    "Tier S = direct fit. Award Tier S when ANY of the following is "
    "explicitly supported by the fund's thesis or portfolio:\n"
    "  - operator-VC whose partners come from paid acquisition / "
    "performance marketing / DTC growth backgrounds;\n"
    "  - explicit thesis on AI ad creative, AI marketing tools, or "
    "creative-AI for performance marketers;\n"
    "  - heavy DTC / eCommerce / SMM / creator-economy portfolio focus "
    "where Kubricon's product is a buyable tool for their existing "
    "portfolio companies.\n"
    "Tier 1 = broad applied-AI fund that writes US pre-seed/seed "
    "checks of $50k-$500k AND has at least one of: SMB SaaS thesis, "
    "creator-tools thesis, or generative-AI / video-AI thesis. "
    "Tier 2 = generic pre-seed friendly fund without an obvious "
    "thesis match but writes our check size. "
    "Use 'watch' for everything else (corporate, late-stage, hardware, "
    "biotech, climate-only, dormant, wrong geography for a US-go-to-"
    "market round, etc.).\n\n"
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
    "generative-video tools end-users actually buy. Always populate "
    "category_tags using the strict slug list provided in the user message - "
    "never invent new slugs."
)


CATEGORIZE_COMPANY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "category_tags": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": KNOWN_COMPANY_TAG_SLUGS,
            },
            "description": (
                "Subset of internal company-category slugs that apply. Be "
                "strict: only emit a slug if the company clearly ships in "
                "that category."
            ),
        },
        "is_kubricon_competitor": {
            "type": "boolean",
            "description": (
                "True only if this company directly competes with Kubricon "
                "(generative video tools end-users actually buy)."
            ),
        },
        "relevance_to_kubricon": {
            "type": "string",
            "description": "1-2 sentence explanation; empty if irrelevant.",
        },
    },
    "required": ["category_tags", "is_kubricon_competitor"],
}


CATEGORIZE_COMPANY_SYSTEM = (
    "You are a venture-data analyst categorising a company we already have "
    "in our CRM. Pick the strict slugs that match what the company actually "
    "ships. If the company is unrelated to creative / AI / dev tooling, "
    "return an empty list. Output JSON only."
)


def build_categorize_company_prompt(*, company) -> str:
    parts = [
        f"Company: {company.name}",
        f"Website: {company.website or 'unknown'}",
        f"HQ: {company.hq or 'unknown'}",
        f"Description:\n{(company.description or '').strip() or 'unknown'}",
        f"Relevance notes:\n{(company.relevance_to_kubricon or '').strip() or '-'}",
        "",
        "Allowed category slugs (pick zero or more):",
        ", ".join(KNOWN_COMPANY_TAG_SLUGS),
    ]
    return "\n".join(parts)


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
    lines.append("")
    lines.append(
        "Category tags - pick every slug that clearly applies "
        "(zero or more, strict slug match):"
    )
    lines.append(", ".join(KNOWN_COMPANY_TAG_SLUGS))
    return "\n".join(lines)
