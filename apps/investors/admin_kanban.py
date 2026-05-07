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
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
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
    OutreachDirection,
    OutreachEvent,
    OutreachOwner,
    Person,
    PipelineStage,
)


CHANNEL_KEYS_DISPLAY = [
    ("form", "Form"),
    ("email", "Email"),
    ("li_dm", "LinkedIn"),
    ("x_dm", "X / Twitter"),
    ("intro", "Warm intro"),
]


COLUMN_DEFS = [
    {
        "key": "research",
        "title": "Research",
        "blurb": "No channel / no draft / no person yet — need manual work.",
        "color": "#6b7280",
    },
    {
        "key": "ready",
        "title": "Ready to send",
        "blurb": "Has channel + draft. Copy and send.",
        "color": "#1d4ed8",
    },
    {
        "key": "sent",
        "title": "Sent",
        "blurb": "Awaiting reply, follow-up not yet due.",
        "color": "#0891b2",
    },
    {
        "key": "followup",
        "title": "Follow-up due",
        "blurb": "7+ days since send, no reply. Send a nudge.",
        "color": "#d97706",
    },
    {
        "key": "replied",
        "title": "Replied",
        "blurb": "They responded. Move to meeting once booked.",
        "color": "#15803d",
    },
    {
        "key": "meeting",
        "title": "Meeting / DD",
        "blurb": "Call booked or in due diligence.",
        "color": "#7c3aed",
    },
    {
        "key": "closed",
        "title": "Closed",
        "blurb": "Won, lost, or no fit. End of pipeline.",
        "color": "#374151",
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
    channel_states: list = field(default_factory=list)
    sent_count: int = 0


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


def _channel_available(person: Person, fund: Fund | None, channel: str) -> bool:
    """Whether the user has the contact details to use this channel."""
    if channel == "form":
        return bool(fund and fund.submission_url)
    if channel == "email":
        return bool((fund and fund.contact_email) or person.email)
    if channel == "li_dm":
        return bool(person.linkedin_url)
    if channel == "x_dm":
        return bool(person.twitter_handle)
    if channel == "intro":
        return True  # always offered (warm intro is people-driven)
    return False


def _person_channel_states(
    person: Person, fund: Fund | None, events_by_channel: dict[str, object] | None = None
) -> list[dict]:
    """Compact list for the card pills."""
    if events_by_channel is None:
        events_by_channel = {}
    states: list[dict] = []
    for key, label in CHANNEL_KEYS_DISPLAY:
        event = events_by_channel.get(key)
        states.append(
            {
                "key": key,
                "label": label,
                "available": _channel_available(person, fund, key),
                "sent": bool(event),
                "sent_at": getattr(event, "sent_at", None),
            }
        )
    return states


def _events_by_channel_for_person(
    person: Person,
) -> dict[str, OutreachEvent]:
    """Latest OUTBOUND event per channel for a person."""
    result: dict[str, OutreachEvent] = {}
    qs = OutreachEvent.objects.filter(
        person=person, direction=OutreachDirection.OUTBOUND
    ).order_by("-sent_at")
    for ev in qs:
        result.setdefault(ev.channel, ev)
    return result


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


def _build_kcard(
    person: Person,
    now,
    events_by_channel: dict[str, OutreachEvent] | None = None,
) -> KCard:
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
    states = _person_channel_states(person, fund, events_by_channel or {})
    sent_count = sum(1 for s in states if s["sent"])
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
        channel_states=states,
        sent_count=sent_count,
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

    # Bulk-load latest outbound event per (person, channel) to avoid N+1.
    person_ids = list(base_qs.values_list("id", flat=True))
    events_map: dict[int, dict[str, OutreachEvent]] = {}
    if person_ids:
        events_qs = OutreachEvent.objects.filter(
            person_id__in=person_ids,
            direction=OutreachDirection.OUTBOUND,
        ).order_by("-sent_at")
        for ev in events_qs:
            per_person = events_map.setdefault(ev.person_id, {})
            per_person.setdefault(ev.channel, ev)

    for p in base_qs.iterator():
        card = _build_kcard(p, now, events_map.get(p.id, {}))
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
        "touch_url": reverse("outreach-kanban-touch"),
    }
    return render(request, "admin/outreach_kanban.html", context)


def _apply_transition(
    person: Person,
    target: str,
    channel: str = "",
    actor=None,
) -> dict:
    """Mutate Person fields + OutreachEvent rows to land in `target` column."""
    now = timezone.now()
    out: dict = {"ok": True, "person_id": person.id}
    fields_to_update: set[str] = set()

    if target == "research":
        OutreachEvent.objects.filter(person=person).delete()
        person.next_followup_at = None
        person.pipeline_stage = PipelineStage.IDENTIFIED
        person.pipeline_changed_at = now
        fields_to_update.update(
            ["next_followup_at", "pipeline_stage", "pipeline_changed_at"]
        )
    elif target == "ready":
        OutreachEvent.objects.filter(person=person).delete()
        person.next_followup_at = None
        person.pipeline_stage = PipelineStage.RESEARCHED
        person.pipeline_changed_at = now
        fields_to_update.update(
            ["next_followup_at", "pipeline_stage", "pipeline_changed_at"]
        )
    elif target == "sent":
        # Keep existing reply events; ensure at least one outbound exists.
        outbound_exists = OutreachEvent.objects.filter(
            person=person, direction=OutreachDirection.OUTBOUND
        ).exists()
        if not outbound_exists:
            ch = channel or _suggested_channel(person, person.fund)
            # Only auto-create an event if we have a real channel to record.
            # If the only available option is "intro" we still record it
            # since warm intros are always conceptually possible.
            if ch and (
                _channel_available(person, person.fund, ch)
                or ch == OutreachChannel.INTRO
            ):
                OutreachEvent.objects.create(
                    person=person,
                    channel=ch,
                    direction=OutreachDirection.OUTBOUND,
                    sent_at=now,
                    actor=actor,
                )
            else:
                return {
                    "ok": False,
                    "error": (
                        "No usable channel for this person. Add at least "
                        "one of email / Twitter / LinkedIn (or a fund "
                        "submission URL) before marking sent."
                    ),
                }
        if person.next_followup_at is None or person.next_followup_at < now:
            person.next_followup_at = now + timedelta(days=7)
            fields_to_update.add("next_followup_at")
        # Drop any reply marker (drag back from Replied)
        OutreachEvent.objects.filter(
            person=person, direction=OutreachDirection.REPLY
        ).delete()
        person.pipeline_stage = PipelineStage.CONTACTED
        person.pipeline_changed_at = now
        fields_to_update.update(["pipeline_stage", "pipeline_changed_at"])
    elif target == "followup":
        if not OutreachEvent.objects.filter(
            person=person, direction=OutreachDirection.OUTBOUND
        ).exists():
            return {
                "ok": False,
                "error": "Cannot move to follow-up before any outreach was sent.",
            }
        person.next_followup_at = now - timedelta(days=1)
        person.pipeline_stage = PipelineStage.CONTACTED
        person.pipeline_changed_at = now
        # Clear any reply marker
        OutreachEvent.objects.filter(
            person=person, direction=OutreachDirection.REPLY
        ).delete()
        fields_to_update.update(
            ["next_followup_at", "pipeline_stage", "pipeline_changed_at"]
        )
    elif target == "replied":
        # Make sure there's at least one outbound event (otherwise it's
        # nonsensical to mark replied)
        if not OutreachEvent.objects.filter(
            person=person, direction=OutreachDirection.OUTBOUND
        ).exists():
            ch = channel or _suggested_channel(person, person.fund) or OutreachChannel.OTHER
            OutreachEvent.objects.create(
                person=person,
                channel=ch,
                direction=OutreachDirection.OUTBOUND,
                sent_at=now,
                actor=actor,
                notes="(auto-created when marked replied)",
            )
        # Mark a reply event
        if not OutreachEvent.objects.filter(
            person=person, direction=OutreachDirection.REPLY
        ).exists():
            OutreachEvent.objects.create(
                person=person,
                channel=channel or person.outreach_channel or OutreachChannel.OTHER,
                direction=OutreachDirection.REPLY,
                sent_at=now,
                actor=actor,
            )
        person.pipeline_stage = PipelineStage.REPLIED
        person.pipeline_changed_at = now
        fields_to_update.update(["pipeline_stage", "pipeline_changed_at"])
    elif target == "meeting":
        if not OutreachEvent.objects.filter(
            person=person, direction=OutreachDirection.REPLY
        ).exists():
            OutreachEvent.objects.create(
                person=person,
                channel=person.outreach_channel or OutreachChannel.OTHER,
                direction=OutreachDirection.REPLY,
                sent_at=now,
                actor=actor,
                notes="(auto-created when moved to meeting)",
            )
        person.pipeline_stage = PipelineStage.MEETING
        person.pipeline_changed_at = now
        fields_to_update.update(["pipeline_stage", "pipeline_changed_at"])
    elif target == "closed":
        person.pipeline_stage = PipelineStage.PASSED
        person.pipeline_changed_at = now
        fields_to_update.update(["pipeline_stage", "pipeline_changed_at"])
    else:
        return {"ok": False, "error": f"Unknown target column: {target}"}

    if fields_to_update:
        fields_to_update.add("updated_at")
        person.save(update_fields=list(fields_to_update))
    person.refresh_from_db()
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
def outreach_kanban_card(request, person_id: int):
    """Return the modal HTML fragment for a single Person card."""
    person = get_object_or_404(
        Person.objects.select_related("fund"), pk=person_id
    )
    fund = person.fund
    draft = _parse_draft(person.outreach_text or "")
    now = timezone.now()
    column = _classify(person, now)
    score = _extract_score(fund.internal_notes if fund else "")
    suggested_channel = _suggested_channel(person, fund)

    days_since_sent = None
    overdue_days = None
    if person.outreach_sent_at:
        days_since_sent = (now - person.outreach_sent_at).days
        if (
            person.next_followup_at
            and person.next_followup_at < now
            and not person.replied_at
        ):
            overdue_days = (now - person.next_followup_at).days

    events_by_channel = _events_by_channel_for_person(person)
    channel_states = _person_channel_states(person, fund, events_by_channel)
    timeline = list(
        OutreachEvent.objects.filter(person=person).order_by("-sent_at")[:30]
    )

    # Pre-resolve admin URLs to avoid template lookups inside fragment
    person_admin_url = reverse(
        "admin:investors_person_change", args=[person.id]
    )
    fund_admin_url = (
        reverse("admin:investors_fund_change", args=[fund.id]) if fund else ""
    )

    ctx = {
        "person": person,
        "fund": fund,
        "score": score,
        "column": column,
        "column_def": next(
            (c for c in COLUMN_DEFS if c["key"] == column), None
        ),
        "draft": draft,
        "suggested_channel": suggested_channel,
        "twitter_url": _twitter_url(person.twitter_handle),
        "days_since_sent": days_since_sent,
        "overdue_days": overdue_days,
        "person_admin_url": person_admin_url,
        "fund_admin_url": fund_admin_url,
        "move_url": reverse("outreach-kanban-move"),
        "touch_url": reverse("outreach-kanban-touch"),
        "channel_states": channel_states,
        "timeline": timeline,
    }
    html = render_to_string("admin/outreach_kanban_card.html", ctx, request=request)
    return HttpResponse(html)


@staff_member_required
@require_POST
def outreach_kanban_touch(request):
    """Toggle an outbound OutreachEvent for (person, channel).

    POST JSON: {"person_id": int, "channel": str}
    Response: {"ok": True, "channel": str, "sent": bool, "sent_at": iso?,
               "column": str, "all_states": [...]}
    """
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        payload = {}

    person_id = payload.get("person_id")
    channel = (payload.get("channel") or "").strip()

    if not person_id or not channel:
        return JsonResponse(
            {"ok": False, "error": "Missing person_id or channel."}, status=400
        )
    if channel not in dict(OutreachChannel.choices):
        return JsonResponse(
            {"ok": False, "error": f"Unknown channel: {channel}"}, status=400
        )

    try:
        person = Person.objects.select_related("fund").get(pk=person_id)
    except Person.DoesNotExist:
        return JsonResponse(
            {"ok": False, "error": "Person not found."}, status=404
        )

    now = timezone.now()
    with transaction.atomic():
        existing = OutreachEvent.objects.filter(
            person=person,
            channel=channel,
            direction=OutreachDirection.OUTBOUND,
        )
        if existing.exists():
            existing.delete()
            sent = False
            sent_at_iso = None
        else:
            # Validate the channel is actually usable before creating an
            # event. Without this the user can mark "email sent" for a
            # person whose email is empty.
            if not _channel_available(person, person.fund, channel):
                hint = {
                    "form": "Add a Submission URL to the fund admin first.",
                    "email": "Add a contact email on the Person or Fund admin first.",
                    "li_dm": "Add a LinkedIn URL on the Person admin first.",
                    "x_dm": "Add a Twitter handle on the Person admin first.",
                    "intro": "Warm intro is always allowed (no contact needed).",
                }.get(channel, "Channel requires a contact field.")
                return JsonResponse(
                    {
                        "ok": False,
                        "error": f"{channel} channel is not available for "
                        f"{person.full_name}. {hint}",
                    },
                    status=400,
                )
            ev = OutreachEvent.objects.create(
                person=person,
                channel=channel,
                direction=OutreachDirection.OUTBOUND,
                sent_at=now,
                actor=request.user if request.user.is_authenticated else None,
            )
            # First touch: also set pipeline_stage to CONTACTED and schedule
            # follow-up if not yet set.
            update_fields: list[str] = []
            if person.pipeline_stage in {
                PipelineStage.IDENTIFIED,
                PipelineStage.RESEARCHED,
            }:
                person.pipeline_stage = PipelineStage.CONTACTED
                person.pipeline_changed_at = now
                update_fields.extend(["pipeline_stage", "pipeline_changed_at"])
            if person.next_followup_at is None:
                person.next_followup_at = now + timedelta(days=7)
                update_fields.append("next_followup_at")
            if update_fields:
                update_fields.append("updated_at")
                person.save(update_fields=update_fields)
            sent = True
            sent_at_iso = ev.sent_at.isoformat()

        person.refresh_from_db()
        column = _classify(person, now)
        events_by_channel = _events_by_channel_for_person(person)
        states = _person_channel_states(person, person.fund, events_by_channel)

    return JsonResponse(
        {
            "ok": True,
            "channel": channel,
            "sent": sent,
            "sent_at": sent_at_iso,
            "column": column,
            "pipeline_stage": person.pipeline_stage,
            "pipeline_stage_display": person.get_pipeline_stage_display(),
            "all_states": [
                {
                    "key": s["key"],
                    "label": s["label"],
                    "available": s["available"],
                    "sent": s["sent"],
                    "sent_at": s["sent_at"].isoformat() if s["sent_at"] else None,
                }
                for s in states
            ],
            "sent_count": sum(1 for s in states if s["sent"]),
        }
    )


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

    actor = request.user if request.user.is_authenticated else None
    with transaction.atomic():
        result = _apply_transition(person, target, channel=channel, actor=actor)
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
