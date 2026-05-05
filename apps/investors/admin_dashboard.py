"""
Outreach dashboard for the admin: a single page that surfaces the
funnel state at a glance so we don't have to navigate Fund / Person /
Tag list views just to know "how many Tier 1 are ready to email".

Mounted at /admin/outreach/dashboard/ via apps/investors/admin.py and
gated by `staff_member_required`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q
from django.shortcuts import render
from django.urls import reverse

from .models import (
    Company,
    Deal,
    Fund,
    FundTier,
    Investment,
    Person,
    PipelineStage,
    Tag,
    TagKind,
)

LLM_SCORE_RE = re.compile(r"score=(\d+)")
LLM_ACTIVE_RE = re.compile(r"active=(True|False)")


@dataclass
class DashboardSection:
    title: str
    description: str
    count: int
    admin_url: str
    rows: list[tuple[str, str, str, str]]  # (name, country, check_max, score)


def _extract_score(text: str | None) -> int | None:
    if not text:
        return None
    m = LLM_SCORE_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def _extract_active(text: str | None) -> bool | None:
    if not text:
        return None
    m = LLM_ACTIVE_RE.search(text)
    if not m:
        return None
    return m.group(1) == "True"


def _format_check(check_max: int | None) -> str:
    if not check_max:
        return "?"
    if check_max >= 1_000_000:
        return f"${check_max / 1_000_000:.1f}M"
    return f"${check_max / 1_000:.0f}k"


def _build_section(
    *,
    title: str,
    description: str,
    queryset,
    admin_url: str,
    limit: int = 10,
) -> DashboardSection:
    funds = list(queryset.order_by("-check_max_usd", "name")[:limit])
    rows: list[tuple[str, str, str, str]] = []
    for f in funds:
        score = _extract_score(f.internal_notes)
        rows.append(
            (
                f.name,
                ", ".join(b for b in (f.hq_city, f.hq_country) if b) or "-",
                _format_check(f.check_max_usd),
                str(score) if score is not None else "-",
            )
        )
    return DashboardSection(
        title=title,
        description=description,
        count=queryset.count(),
        admin_url=admin_url,
        rows=rows,
    )


@staff_member_required
def outreach_dashboard(request):
    """One-page summary of the investor funnel + LLM-scoring state."""
    fund_url = reverse("admin:investors_fund_changelist")
    person_url = reverse("admin:investors_person_changelist")
    company_url = reverse("admin:investors_company_changelist")

    # Top-level counters.
    total_funds = Fund.objects.count()
    funds_with_thesis = Fund.objects.exclude(thesis_summary="").count()
    tier_counts = {
        choice[0]: Fund.objects.filter(tier=choice[0]).count()
        for choice in FundTier.choices
    }
    tagged_funds = Fund.objects.filter(thesis_tags__isnull=False).distinct().count()
    untagged_funds = Fund.objects.filter(thesis_tags__isnull=True).count()
    total_people = Person.objects.count()
    contactable_people = Person.objects.exclude(email="").count()
    total_companies = Company.objects.count()
    competitor_companies = Company.objects.filter(is_kubricon_competitor=True).count()
    total_deals = Deal.objects.count()
    total_investments = Investment.objects.count()

    pipeline_counts = {
        choice[0]: Person.objects.filter(pipeline_stage=choice[0]).count()
        for choice in PipelineStage.choices
    }

    # Sections for quick triage.
    sections = [
        _build_section(
            title="Tier S — direct video-AI fit",
            description="Funds whose thesis or portfolio explicitly maps to "
            "generative video, creator AI or video infrastructure. Top "
            "priority for outreach.",
            queryset=Fund.objects.filter(tier=FundTier.S),
            admin_url=f"{fund_url}?tier__exact=S",
            limit=20,
        ),
        _build_section(
            title="Tier 1 — broad applied AI",
            description="Generalist applied-AI funds with pre-seed / seed "
            "appetite. Strong fit for Kubricon's $2M round.",
            queryset=Fund.objects.filter(tier=FundTier.T1),
            admin_url=f"{fund_url}?tier__exact=1",
        ),
        _build_section(
            title="Tier 2 — pre-seed friendly",
            description="Loose thesis fit but writes pre-seed / seed checks "
            "in our range. Useful for second-wave outreach.",
            queryset=Fund.objects.filter(tier=FundTier.T2),
            admin_url=f"{fund_url}?tier__exact=2",
        ),
    ]

    # Quick saved-search shortcuts.
    saved_searches = [
        (
            "Tier 1 USA, ≤$500k pre-seed",
            f"{fund_url}?tier__exact=1&hq_country=USA&check_min_usd__lte=500000",
        ),
        (
            "Tier 1 EU + UK",
            f"{fund_url}?tier__exact=1&hq_country__in=United+Kingdom%2CGermany%2CFrance%2CNetherlands%2CSweden",
        ),
        (
            "Tier 2 active in 2025",
            f"{fund_url}?tier__exact=2&last_activity_at__gte=2025-01-01",
        ),
        (
            "Untagged funds (need review)",
            f"{fund_url}?thesis_tags__isnull=True",
        ),
        (
            "Persons in pipeline (any stage past Identified)",
            f"{person_url}?pipeline_stage__in=researched,contacted,replied,meeting,dd,term_sheet",
        ),
        (
            "Kubricon competitors",
            f"{company_url}?is_kubricon_competitor__exact=1",
        ),
    ]

    context = {
        "title": "Outreach dashboard",
        "site_header": "Kubricon Investor CRM",
        "fund_url": fund_url,
        "person_url": person_url,
        "company_url": company_url,
        "total_funds": total_funds,
        "funds_with_thesis": funds_with_thesis,
        "tier_counts": tier_counts,
        "tagged_funds": tagged_funds,
        "untagged_funds": untagged_funds,
        "total_people": total_people,
        "contactable_people": contactable_people,
        "total_companies": total_companies,
        "competitor_companies": competitor_companies,
        "total_deals": total_deals,
        "total_investments": total_investments,
        "pipeline_counts": pipeline_counts,
        "sections": sections,
        "saved_searches": saved_searches,
    }
    return render(request, "admin/outreach_dashboard.html", context)
