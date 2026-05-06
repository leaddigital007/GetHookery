"""
Outreach worklist - the page Igor and his partner actually open
every morning. Shows a prioritised queue of who to contact today,
who is overdue for a follow-up, who has replied, and lets them mark
each step done in one click.

Mounted at /admin/outreach/worklist/ from config/urls.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from .models import (
    Fund,
    FundTier,
    OutreachChannel,
    OutreachOwner,
    Person,
    PipelineStage,
)

DRAFT_MARKER = "=== LLM DRAFT (do not send as-is) ==="
DRAFT_END_MARKER = "=== /LLM DRAFT ==="
PRIMARY_RE = re.compile(r"\[PRIMARY\]")
LLM_SCORE_RE = re.compile(r"score=(\d+)")
DRAFT_CONFIDENCE_RE = re.compile(r"confidence:\s*(high|medium|low)")
HOOK_RE = re.compile(r"hook:\s*(.+)")
SECTION_RE = {
    "dm_short": re.compile(
        r"--- X / Twitter DM \(<=270 chars\) ---\n(.+?)\n\n--- LinkedIn DM",
        re.S,
    ),
    "dm_long": re.compile(
        r"--- LinkedIn DM \(~600-1100 chars\) ---\n(.+?)\n\n--- Email ---",
        re.S,
    ),
    "email_subject": re.compile(r"--- Email ---\nSubject:\s*(.+)"),
    "email_body": re.compile(
        r"--- Email ---\nSubject:.*?\n\n(.+?)\n\n=== /LLM DRAFT ===",
        re.S,
    ),
}


@dataclass
class Card:
    """One row in the worklist - everything a human needs to act."""

    person: Person
    fund: Fund | None
    fund_score: int | None
    primary: bool
    is_high_conf: bool
    confidence: str
    hook: str
    dm_short: str
    dm_long: str
    email_subject: str
    email_body: str
    suggested_channel: str
    submission_url: str
    contact_email: str
    twitter_url: str
    linkedin_url: str
    sent_label: str = ""
    overdue: bool = False
    awaiting_reply: bool = False


def _extract_score(text: str | None) -> int | None:
    if not text:
        return None
    m = LLM_SCORE_RE.search(text)
    return int(m.group(1)) if m else None


def _parse_draft(outreach_text: str) -> dict[str, str]:
    if not outreach_text or DRAFT_MARKER not in outreach_text:
        return {}
    m_conf = DRAFT_CONFIDENCE_RE.search(outreach_text)
    m_hook = HOOK_RE.search(outreach_text)
    out: dict[str, str] = {
        "confidence": (m_conf.group(1) if m_conf else "low"),
        "hook": (m_hook.group(1).strip() if m_hook else ""),
    }
    for key, pat in SECTION_RE.items():
        m = pat.search(outreach_text)
        out[key] = (m.group(1).strip() if m else "")
    return out


def _suggested_channel(person: Person, fund: Fund | None) -> str:
    """Pick the best outbound channel given what we know."""
    if fund and fund.submission_url:
        return "form"
    if fund and fund.contact_email:
        return "email"
    if person.linkedin_url:
        return "li_dm"
    if person.twitter_handle:
        return "x_dm"
    if person.email:
        return "email"
    return ""


def _twitter_url(handle: str) -> str:
    handle = (handle or "").lstrip("@").strip()
    return f"https://x.com/{handle}" if handle else ""


def _build_card(person: Person) -> Card:
    fund = person.fund
    draft = _parse_draft(person.outreach_text or "")
    confidence = draft.get("confidence", "low")
    primary = bool(PRIMARY_RE.search(person.internal_notes or ""))
    sent_label = ""
    overdue = False
    awaiting = False
    now = timezone.now()
    if person.outreach_sent_at:
        days = (now - person.outreach_sent_at).days
        sent_label = f"sent {days}d ago"
        if person.replied_at is None:
            awaiting = True
        if (
            person.next_followup_at
            and person.next_followup_at < now
            and person.replied_at is None
        ):
            overdue = True
    return Card(
        person=person,
        fund=fund,
        fund_score=_extract_score(fund.internal_notes if fund else ""),
        primary=primary,
        is_high_conf=(confidence == "high"),
        confidence=confidence,
        hook=draft.get("hook", ""),
        dm_short=draft.get("dm_short", ""),
        dm_long=draft.get("dm_long", ""),
        email_subject=draft.get("email_subject", ""),
        email_body=draft.get("email_body", ""),
        suggested_channel=_suggested_channel(person, fund),
        submission_url=(fund.submission_url if fund else ""),
        contact_email=(fund.contact_email if fund else ""),
        twitter_url=_twitter_url(person.twitter_handle),
        linkedin_url=person.linkedin_url,
        sent_label=sent_label,
        overdue=overdue,
        awaiting_reply=awaiting,
    )


@dataclass
class Bucket:
    title: str
    description: str
    cards: list[Card] = field(default_factory=list)
    fund_rows: list = field(default_factory=list)
    style: str = "default"


@dataclass
class DarkFundRow:
    """Fund without any actionable contact - rendered as a 'needs research' row."""

    fund: Fund
    score: int | None
    reason: str
    has_persons: bool
    persons_summary: str


def _ranking_key(card: Card) -> tuple:
    """Higher = earlier in the queue."""
    fund = card.fund
    tier_rank = {"S": 4, "1": 3, "2": 2, "watch": 1}.get(
        fund.tier if fund else "watch", 0
    )
    return (
        1 if card.primary else 0,
        1 if card.is_high_conf else 0,
        tier_rank,
        card.fund_score or 0,
        (fund.check_max_usd or 0) if fund else 0,
    )


@staff_member_required
def outreach_worklist(request):
    if request.method == "POST":
        return _handle_post(request)

    owner = (request.GET.get("owner") or "").strip()
    tiers_filter = request.GET.get("tiers", "S,1")
    tiers = [t.strip() for t in tiers_filter.split(",") if t.strip()]
    search_q = (request.GET.get("q") or "").strip()

    base_qs = (
        Person.objects.select_related("fund")
        .filter(fund__tier__in=tiers)
        .filter(full_name__gt="")
    )
    if owner == "unassigned":
        base_qs = base_qs.filter(assigned_to="")
    elif owner in {"igor", "partner", "shared"}:
        base_qs = base_qs.filter(assigned_to=owner)
    if search_q:
        base_qs = base_qs.filter(
            Q(full_name__icontains=search_q)
            | Q(fund__name__icontains=search_q)
            | Q(role__icontains=search_q)
        )

    today_qs = base_qs.filter(outreach_sent_at__isnull=True)
    overdue_qs = base_qs.filter(
        outreach_sent_at__isnull=False,
        next_followup_at__lt=timezone.now(),
        replied_at__isnull=True,
    )
    awaiting_qs = base_qs.filter(
        outreach_sent_at__isnull=False, replied_at__isnull=True
    )
    replied_qs = base_qs.filter(replied_at__isnull=False)

    today_cards = sorted(
        (_build_card(p) for p in today_qs),
        key=_ranking_key,
        reverse=True,
    )
    primary_cards = [c for c in today_cards if c.primary]
    other_cards = [c for c in today_cards if not c.primary]

    overdue_cards = sorted(
        (_build_card(p) for p in overdue_qs),
        key=_ranking_key,
        reverse=True,
    )
    awaiting_cards = sorted(
        (_build_card(p) for p in awaiting_qs),
        key=_ranking_key,
        reverse=True,
    )
    replied_cards = sorted(
        (_build_card(p) for p in replied_qs),
        key=_ranking_key,
        reverse=True,
    )

    # Funds with persons but no fund-level channel + no person-level channel
    # OR Funds with absolutely no Person attached. Both buckets need manual
    # research from a human. Funds explicitly skipped (marker in
    # internal_notes) are hidden so the buckets stay actionable.
    dark_funds_qs = (
        Fund.objects.filter(tier__in=tiers)
        .annotate(person_count=Count("people"))
        .exclude(internal_notes__contains="[outreach_skipped:")
    )
    if search_q:
        dark_funds_qs = dark_funds_qs.filter(name__icontains=search_q)

    no_person_funds = dark_funds_qs.filter(person_count=0)
    no_channel_funds = dark_funds_qs.filter(
        person_count__gt=0,
        submission_url="",
        contact_email="",
    )
    # Filter the second bucket to those whose persons also lack DM channels.
    half_dark_rows: list[DarkFundRow] = []
    for f in no_channel_funds:
        persons = list(
            Person.objects.filter(fund=f).values(
                "full_name", "twitter_handle", "linkedin_url", "email"
            )
        )
        any_dm = any(p["twitter_handle"] or p["linkedin_url"] or p["email"]
                     for p in persons)
        if not any_dm:
            summary = ", ".join(p["full_name"] for p in persons[:3])
            half_dark_rows.append(
                DarkFundRow(
                    fund=f,
                    score=_extract_score(f.internal_notes),
                    reason="Fund has no submission URL / email and partners have no DM channels.",
                    has_persons=True,
                    persons_summary=summary,
                )
            )
    half_dark_rows.sort(
        key=lambda r: (
            {"S": 4, "1": 3, "2": 2, "watch": 1}.get(r.fund.tier, 0),
            r.fund.check_max_usd or 0,
        ),
        reverse=True,
    )

    no_person_rows = sorted(
        (
            DarkFundRow(
                fund=f,
                score=_extract_score(f.internal_notes),
                reason=(
                    "No partner-level contacts on file. Add at least one "
                    "Person for this fund or skip / pass."
                ),
                has_persons=False,
                persons_summary="",
            )
            for f in no_person_funds
        ),
        key=lambda r: (
            {"S": 4, "1": 3, "2": 2, "watch": 1}.get(r.fund.tier, 0),
            r.fund.check_max_usd or 0,
        ),
        reverse=True,
    )

    buckets = [
        Bucket(
            title="Today: primary contacts",
            description=(
                "Tier-S and Tier-1 fund partners marked as the best first "
                "point of contact. Start here. Each row has a personalised "
                "draft you can copy, the channel link, and a 'Mark sent' "
                "button that timestamps the action and schedules a "
                "+7-day follow-up."
            ),
            cards=primary_cards,
            style="primary",
        ),
        Bucket(
            title="Today: other partners (same funds, second contact)",
            description=(
                "Use these only after the primary contact has not "
                "responded for 5-7 days, or as a parallel touch on a "
                "different channel."
            ),
            cards=other_cards,
            style="other",
        ),
        Bucket(
            title="Funds without partner contacts (manual research needed)",
            description=(
                "These funds passed the LLM scoring but we don't have a "
                "single person on file yet. Either add a partner manually "
                "from the fund admin (use LinkedIn / fund's /team page) "
                "or skip to deprioritise."
            ),
            fund_rows=no_person_rows,
            style="dark",
        ),
        Bucket(
            title="Funds with persons but no channel (DM hunting)",
            description=(
                "We know the partner names but have no Twitter / LinkedIn "
                "/ email for them. Add one channel per row to unlock the "
                "draft and 'Mark sent' button."
            ),
            fund_rows=half_dark_rows,
            style="dark",
        ),
        Bucket(
            title="Follow-ups overdue",
            description=(
                "You marked these sent and a follow-up was scheduled - "
                "the date has passed and they have not replied. Send a "
                "short nudge."
            ),
            cards=overdue_cards,
            style="overdue",
        ),
        Bucket(
            title="Awaiting reply",
            description=(
                "Outreach sent, follow-up date is still in the future. "
                "Just here so you don't forget about them."
            ),
            cards=awaiting_cards,
            style="awaiting",
        ),
        Bucket(
            title="Replied",
            description=(
                "Conversation started. Move them through the pipeline "
                "via the Person admin (Researched -> Contacted -> "
                "Replied -> Meeting -> DD)."
            ),
            cards=replied_cards,
            style="replied",
        ),
    ]

    counters = {
        "today_primary": len(primary_cards),
        "today_other": len(other_cards),
        "overdue": len(overdue_cards),
        "awaiting": len(awaiting_cards),
        "replied": len(replied_cards),
        "no_persons": len(no_person_rows),
        "half_dark": len(half_dark_rows),
        "all_today": today_qs.count(),
    }

    owner_options = [
        ("", "All owners"),
        ("unassigned", "Unassigned"),
        ("igor", "Igor"),
        ("partner", "Partner"),
        ("shared", "Shared"),
    ]
    tier_options = [
        ("S", "Tier S only"),
        ("S,1", "Tier S + 1 (default)"),
        ("S,1,2", "Tier S + 1 + 2"),
    ]

    context = {
        "title": "Outreach worklist",
        "site_header": "Kubricon Investor CRM",
        "buckets": buckets,
        "counters": counters,
        "owner_options": owner_options,
        "owner": owner,
        "tier_options": tier_options,
        "tiers_filter": tiers_filter,
        "search_q": search_q,
        "person_admin_base": reverse("admin:investors_person_changelist"),
        "person_add_url": reverse("admin:investors_person_add"),
    }
    return render(request, "admin/outreach_worklist.html", context)


def _handle_post(request):
    """Handle the inline action buttons (mark sent, schedule, replied, assign, skip_fund)."""
    action = (request.POST.get("action") or "").strip()
    person_id = request.POST.get("person_id")
    fund_id = request.POST.get("fund_id")
    channel = (request.POST.get("channel") or "").strip()
    owner = (request.POST.get("owner") or "").strip()
    next_url = request.POST.get("next") or request.path

    if action == "skip_fund":
        if not fund_id:
            messages.error(request, "Missing fund id.")
            return HttpResponseRedirect(next_url)
        try:
            fund = Fund.objects.get(pk=fund_id)
        except Fund.DoesNotExist:
            messages.error(request, "Fund not found.")
            return HttpResponseRedirect(next_url)
        marker = f"[outreach_skipped: {timezone.now().date().isoformat()}]"
        if marker not in (fund.internal_notes or ""):
            fund.internal_notes = (
                (fund.internal_notes or "").rstrip() + "\n" + marker
            ).lstrip()
            fund.save(update_fields=["internal_notes", "updated_at"])
        messages.info(
            request,
            f"{fund.name} marked as skipped — it will no longer appear in "
            "the dark-funds buckets. You can revert from the Fund admin "
            "by removing the [outreach_skipped:...] line in internal_notes.",
        )
        return HttpResponseRedirect(next_url)

    if not person_id:
        messages.error(request, "Missing person id.")
        return HttpResponseRedirect(next_url)

    try:
        person = Person.objects.select_related("fund").get(pk=person_id)
    except Person.DoesNotExist:
        messages.error(request, "Person not found.")
        return HttpResponseRedirect(next_url)

    now = timezone.now()
    with transaction.atomic():
        if action == "mark_sent":
            if not channel:
                channel = _suggested_channel(person, person.fund)
            person.outreach_channel = channel or OutreachChannel.OTHER
            person.outreach_sent_at = now
            person.pipeline_stage = PipelineStage.CONTACTED
            person.pipeline_changed_at = now
            person.next_followup_at = now + timezone.timedelta(days=7)
            person.save(
                update_fields=[
                    "outreach_channel",
                    "outreach_sent_at",
                    "pipeline_stage",
                    "pipeline_changed_at",
                    "next_followup_at",
                    "updated_at",
                ]
            )
            messages.success(
                request,
                f"{person.full_name} marked sent via {channel or 'channel'}; "
                "follow-up in 7 days.",
            )
        elif action == "mark_followup_sent":
            person.next_followup_at = now + timezone.timedelta(days=7)
            person.save(update_fields=["next_followup_at", "updated_at"])
            messages.success(
                request, f"{person.full_name} follow-up rescheduled +7d."
            )
        elif action == "mark_replied":
            person.replied_at = now
            person.pipeline_stage = PipelineStage.REPLIED
            person.pipeline_changed_at = now
            person.save(
                update_fields=[
                    "replied_at",
                    "pipeline_stage",
                    "pipeline_changed_at",
                    "updated_at",
                ]
            )
            messages.success(
                request, f"{person.full_name} marked replied. Move them along."
            )
        elif action == "skip":
            person.pipeline_stage = PipelineStage.PASSED
            person.pipeline_changed_at = now
            person.save(
                update_fields=[
                    "pipeline_stage",
                    "pipeline_changed_at",
                    "updated_at",
                ]
            )
            messages.info(request, f"{person.full_name} skipped (Passed).")
        elif action == "assign":
            person.assigned_to = owner if owner in {
                OutreachOwner.IGOR,
                OutreachOwner.PARTNER,
                OutreachOwner.SHARED,
                OutreachOwner.UNASSIGNED,
            } else ""
            person.save(update_fields=["assigned_to", "updated_at"])
            messages.success(
                request,
                f"{person.full_name} assigned to {person.get_assigned_to_display()}.",
            )
        else:
            messages.error(request, f"Unknown action: {action}")

    return HttpResponseRedirect(next_url)
