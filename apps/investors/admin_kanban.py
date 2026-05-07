"""
Outreach Kanban board.

Mounted at /admin/outreach/board/.

Why a Kanban instead of stacked buckets:
the user (Igor + partner) needs to see the entire pipeline in one
viewport with drag-and-drop status changes. Each column corresponds to
a derived stage; dragging a card writes the underlying Person fields so
the data model stays consistent with worklist + sent log + admin
filters.

Derived stages (column -> rule):

  research   pipeline_stage in {IDENTIFIED, RESEARCHED} OR no channel yet
             AND outreach_sent_at IS NULL
  ready      has at least one channel AND outreach_text contains a draft
             AND outreach_sent_at IS NULL
  sent       outreach_sent_at IS NOT NULL AND replied_at IS NULL
             AND (next_followup_at IS NULL OR next_followup_at >= now)
  followup   outreach_sent_at IS NOT NULL AND replied_at IS NULL
             AND next_followup_at < now (overdue)
  replied    pipeline_stage == REPLIED OR replied_at IS NOT NULL (and
             not yet moved to meeting / closed)
  meeting    pipeline_stage in {MEETING, DD, TERM_SHEET}
  closed     pipeline_stage in {CLOSED_WON, CLOSED_LOST, PASSED}

Drag side-effects (target column -> field mutations):

  research   outreach_sent_at=None, replied_at=None, next_followup_at=None,
             pipeline_stage=IDENTIFIED
  ready      outreach_sent_at=None, replied_at=None, next_followup_at=None,
             pipeline_stage=RESEARCHED
  sent       if outreach_sent_at is None -> outreach_sent_at=now,
             pipeline_stage=CONTACTED, next_followup_at=now+7
  followup   force overdue: next_followup_at = now - 1 day,
             keep outreach_sent_at, pipeline_stage=CONTACTED
  replied    replied_at=now, pipeline_stage=REPLIED
  meeting    pipeline_stage=MEETING
  closed     pipeline_stage=PASSED (default 'no fit'); user can refine
             from Person admin to CLOSED_WON / CLOSED_LOST.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .admin_worklist import (
    _extract_score,
    _parse_draft,
    _suggested_channel,
    _twitter_url,
)
from .models import (
    Fund,
    FundTier,
    OutreachChannel,
    OutreachOwner,
    Person,
    PipelineStage,
)


COLUMN_DEFS = [
    {
        "key": "research",
        "title": "Research",
        "blurb": "No channel / no draft / no person yet — need manual work.",
        "color": "#6b7280",
        "accept": True,
    },
    {
        "key": "ready",
        "title": "Ready to send",
        "blurb": "Has channel + draft. Copy and send.",
        "color": "#1d4ed8",
        "accept": True,
    },
    {
        "key": "sent",
        "title": "Sent",
        "blurb": "Awaiting reply, follow-up not yet due.",
        "color": "#0891b2",
        "accept": True,
    },
    {
        "key": "followup",
        "title": "Follow-up due",
        "blurb": "7+ days since send, no reply. Send a nudge.",
        "color": "#d97706",
        "accept": True,
    },
    {
        "key": "replied",
        "title": "Replied",
        "blurb": "They responded. Move to meeting once booked.",
        "color": "#15803d",
        "accept": True,
    },
    {
        "key": "meeting",
        "title": "Meeting / DD",
        "blurb": "Call booked or in due diligence.",
        "color": "#7c3aed",
        "accept": True,
    },
    {
        "key": "closed",
        "title": "Closed",
        "blurb": "Won, lost, or no fit. End of pipeline.",
        "color": "#374151",
        "accept": True,
    },
]

CLOSED_STAGES = {
    PipelineStage.CLOSED_WON,
    PipelineStage.CLOSED_LOST,
    PipelineStage.PASSED,
}
MEETING_STAGES = {
    PipelineStage.MEETING,
    PipelineStage.DD,
    PipelineStage.TERM_SHEET,
}


@dataclass
class KCard:
    person: Person
    fund: Fund | None
    fund_score: int | None
    column: str
    confidence: str
    hook: str
    suggested_channel: str
    has_draft: bool
    has_channel: bool
    submission_url: str
    contact_email: str
    twitter_url: str
    linkedin_url: str
    days_since_sent: int | None
    overdue_days: int | None


@dataclass
class KFundCard:
    """Fund-level card shown in Research column when no Person exists."""

    fund: Fund
    score: int | None
    reason: str


@dataclass
class KColumn:
    key: str
    title: str
    blurb: str
    color: str
    cards: list = field(default_factory=list)
    fund_cards: list = field(default_factory=list)


def _classify(person: Person, now) -> str:
    if person.pipeline_stage in CLOSED_STAGES:
        return "closed"
    if person.pipeline_stage in MEETING_STAGES:
        return "meeting"
    if person.replied_at or person.pipeline_stage == PipelineStage.REPLIED:
        return "replied"
    if person.outreach_sent_at:
        if (
            person.next_followup_at
            and person.next_followup_at < now
            and not person.replied_at
        ):
            return "followup"
        return "sent"
    has_channel = bool(
        person.email
        or person.twitter_handle
        or person.linkedin_url
        or (person.fund and person.fund.submission_url)
        or (person.fund and person.fund.contact_email)
    )
    has_draft = "=== LLM DRAFT" in (person.outreach_text or "")
    if has_channel and has_draft:
        return "ready"
    return "research"


def _build_kcard(person: Person, now) -> KCard:
    fund = person.fund
    draft = _parse_draft(person.outreach_text or "")
    column = _classify(person, now)
    has_channel = bool(
        person.email
        or person.twitter_handle
        or person.linkedin_url
        or (fund and fund.submission_url)
        or (fund and fund.contact_email)
    )
    has_draft = "=== LLM DRAFT" in (person.outreach_text or "")
    days_sent = None
    overdue_days = None
    if person.outreach_sent_at:
        days_sent = (now - person.outreach_sent_at).days
        if (
            person.next_followup_at
            and person.next_followup_at < now
            and not person.replied_at
        ):
            overdue_days = (now - person.next_followup_at).days
    return KCard(
        person=person,
        fund=fund,
        fund_score=_extract_score(fund.internal_notes if fund else ""),
        column=column,
        confidence=draft.get("confidence", ""),
        hook=draft.get("hook", ""),
        suggested_channel=_suggested_channel(person, fund),
        has_draft=has_draft,
        has_channel=has_channel,
        submission_url=(fund.submission_url if fund else ""),
        contact_email=(fund.contact_email if fund else ""),
        twitter_url=_twitter_url(person.twitter_handle),
        linkedin_url=person.linkedin_url,
        days_since_sent=days_sent,
        overdue_days=overdue_days,
    )


def _column_sort_key(card: KCard) -> tuple:
    """Inside each column, sort by tier > score > recency."""
    fund = card.fund
    tier_rank = {"S": 4, "1": 3, "2": 2, "watch": 1}.get(
        fund.tier if fund else "watch", 0
    )
    score = card.fund_score or 0
    # Overdue first inside follow-up; oldest sent first inside sent column.
    if card.column == "followup":
        recency = -(card.overdue_days or 0)
    elif card.column == "sent":
        recency = -(card.days_since_sent or 0)
    else:
        recency = 0
    return (tier_rank, score, recency)


@staff_member_required
def outreach_kanban(request):
    if request.method == "POST":
        return _handle_post_form(request)

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

    now = timezone.now()
    columns = {c["key"]: KColumn(**c) for c in COLUMN_DEFS}

    for p in base_qs.iterator():
        card = _build_kcard(p, now)
        if card.column in columns:
            columns[card.column].cards.append(card)

    for col in columns.values():
        col.cards.sort(key=_column_sort_key, reverse=True)

    # Fund-level dark cards for "Research" column.
    dark_funds = (
        Fund.objects.filter(tier__in=tiers)
        .annotate(person_count=Count("people"))
        .filter(person_count=0)
        .exclude(internal_notes__contains="[outreach_skipped:")
    )
    if search_q:
        dark_funds = dark_funds.filter(name__icontains=search_q)
    fund_cards = sorted(
        (
            KFundCard(
                fund=f,
                score=_extract_score(f.internal_notes),
                reason="No partner contacts on file. Add 1-2 partners "
                "from the fund's /team page.",
            )
            for f in dark_funds
        ),
        key=lambda fc: (
            {"S": 4, "1": 3, "2": 2, "watch": 1}.get(fc.fund.tier, 0),
            fc.score or 0,
            fc.fund.check_max_usd or 0,
        ),
        reverse=True,
    )
    columns["research"].fund_cards = fund_cards

    counters = {k: len(v.cards) for k, v in columns.items()}
    counters["research_funds"] = len(fund_cards)
    counters["total"] = sum(counters[c["key"]] for c in COLUMN_DEFS)

    context = {
        "title": "Outreach Kanban",
        "site_header": "Kubricon Investor CRM",
        "columns": [columns[c["key"]] for c in COLUMN_DEFS],
        "counters": counters,
        "owner": owner,
        "owner_options": [
            ("", "All owners"),
            ("igor", "Igor"),
            ("partner", "Partner"),
            ("shared", "Shared"),
            ("unassigned", "Unassigned"),
        ],
        "tiers_filter": tiers_filter,
        "tier_options": [
            ("S", "Tier S only"),
            ("S,1", "Tier S + 1"),
            ("S,1,2", "Tier S + 1 + 2"),
        ],
        "search_q": search_q,
        "person_admin_base": reverse("admin:investors_person_changelist"),
        "person_add_url": reverse("admin:investors_person_add"),
        "move_url": reverse("outreach-kanban-move"),
    }
    return render(request, "admin/outreach_kanban.html", context)


def _apply_transition(person: Person, target: str, channel: str = "") -> dict:
    """Mutate Person fields to land it in `target` column. Idempotent."""
    now = timezone.now()
    out: dict = {"ok": True, "person_id": person.id}
    fields_to_update: set[str] = set()

    if target == "research":
        person.outreach_sent_at = None
        person.replied_at = None
        person.next_followup_at = None
        person.pipeline_stage = PipelineStage.IDENTIFIED
        person.pipeline_changed_at = now
        fields_to_update.update(
            [
                "outreach_sent_at",
                "replied_at",
                "next_followup_at",
                "pipeline_stage",
                "pipeline_changed_at",
            ]
        )
    elif target == "ready":
        person.outreach_sent_at = None
        person.replied_at = None
        person.next_followup_at = None
        person.pipeline_stage = PipelineStage.RESEARCHED
        person.pipeline_changed_at = now
        fields_to_update.update(
            [
                "outreach_sent_at",
                "replied_at",
                "next_followup_at",
                "pipeline_stage",
                "pipeline_changed_at",
            ]
        )
    elif target == "sent":
        if person.outreach_sent_at is None:
            person.outreach_sent_at = now
            person.next_followup_at = now + timedelta(days=7)
            fields_to_update.update(["outreach_sent_at", "next_followup_at"])
            if channel:
                person.outreach_channel = channel
                fields_to_update.add("outreach_channel")
            elif not person.outreach_channel:
                suggested = _suggested_channel(person, person.fund)
                person.outreach_channel = suggested or OutreachChannel.OTHER
                fields_to_update.add("outreach_channel")
        elif person.next_followup_at is None or person.next_followup_at < now:
            person.next_followup_at = now + timedelta(days=7)
            fields_to_update.add("next_followup_at")
        person.replied_at = None
        person.pipeline_stage = PipelineStage.CONTACTED
        person.pipeline_changed_at = now
        fields_to_update.update(
            ["replied_at", "pipeline_stage", "pipeline_changed_at"]
        )
    elif target == "followup":
        if person.outreach_sent_at is None:
            return {
                "ok": False,
                "error": "Cannot move to follow-up before marking sent.",
            }
        person.next_followup_at = now - timedelta(days=1)
        person.replied_at = None
        person.pipeline_stage = PipelineStage.CONTACTED
        person.pipeline_changed_at = now
        fields_to_update.update(
            [
                "next_followup_at",
                "replied_at",
                "pipeline_stage",
                "pipeline_changed_at",
            ]
        )
    elif target == "replied":
        if person.outreach_sent_at is None:
            person.outreach_sent_at = now
            fields_to_update.add("outreach_sent_at")
        person.replied_at = now
        person.pipeline_stage = PipelineStage.REPLIED
        person.pipeline_changed_at = now
        fields_to_update.update(
            ["replied_at", "pipeline_stage", "pipeline_changed_at"]
        )
    elif target == "meeting":
        if not person.replied_at:
            person.replied_at = now
            fields_to_update.add("replied_at")
        person.pipeline_stage = PipelineStage.MEETING
        person.pipeline_changed_at = now
        fields_to_update.update(["pipeline_stage", "pipeline_changed_at"])
    elif target == "closed":
        person.pipeline_stage = PipelineStage.PASSED
        person.pipeline_changed_at = now
        fields_to_update.update(["pipeline_stage", "pipeline_changed_at"])
    else:
        return {"ok": False, "error": f"Unknown target column: {target}"}

    fields_to_update.add("updated_at")
    person.save(update_fields=list(fields_to_update))
    out["new_column"] = target
    out["pipeline_stage"] = person.pipeline_stage
    out["pipeline_stage_display"] = person.get_pipeline_stage_display()
    if person.outreach_sent_at:
        out["sent_at"] = person.outreach_sent_at.isoformat()
    if person.replied_at:
        out["replied_at"] = person.replied_at.isoformat()
    if person.next_followup_at:
        out["next_followup_at"] = person.next_followup_at.isoformat()
    return out


@staff_member_required
@require_POST
def outreach_kanban_move(request):
    """AJAX endpoint: receive {person_id, target, [channel]} -> 200 / 400."""
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        payload = {}

    person_id = payload.get("person_id") or request.POST.get("person_id")
    target = (payload.get("target") or request.POST.get("target") or "").strip()
    channel = (payload.get("channel") or request.POST.get("channel") or "").strip()

    if not person_id or not target:
        return JsonResponse(
            {"ok": False, "error": "Missing person_id or target."}, status=400
        )

    try:
        person = Person.objects.select_related("fund").get(pk=person_id)
    except Person.DoesNotExist:
        return JsonResponse(
            {"ok": False, "error": "Person not found."}, status=404
        )

    with transaction.atomic():
        result = _apply_transition(person, target, channel=channel)
    if not result.get("ok"):
        return JsonResponse(result, status=400)
    return JsonResponse(result)


def _handle_post_form(request):
    """Fallback non-AJAX handler (e.g. for skip_fund button on dark cards)."""
    action = (request.POST.get("action") or "").strip()
    next_url = request.POST.get("next") or request.path

    if action == "skip_fund":
        fund_id = request.POST.get("fund_id")
        if fund_id:
            try:
                fund = Fund.objects.get(pk=fund_id)
            except Fund.DoesNotExist:
                return HttpResponseRedirect(next_url)
            marker = f"[outreach_skipped: {timezone.now().date().isoformat()}]"
            if marker not in (fund.internal_notes or ""):
                fund.internal_notes = (
                    (fund.internal_notes or "").rstrip() + "\n" + marker
                ).lstrip()
                fund.save(update_fields=["internal_notes", "updated_at"])
        return HttpResponseRedirect(next_url)
    return HttpResponseRedirect(next_url)
