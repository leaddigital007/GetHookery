"""
Sent-log view: chronological feed of every outreach action you have taken.
Mounted at /admin/outreach/sent/.

Answers the user question: "После того как я отписал письма - где потом
смотреть списки партнеров которым я уже отправил все?"
"""
from __future__ import annotations

from datetime import timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from .models import OutreachOwner, Person


@staff_member_required
def outreach_sent_log(request):
    """A single chronological feed of every Person we have contacted."""
    owner = (request.GET.get("owner") or "").strip()
    status = (request.GET.get("status") or "all").strip()
    search_q = (request.GET.get("q") or "").strip()
    days = int(request.GET.get("days") or 0)

    qs = (
        Person.objects.select_related("fund")
        .filter(outreach_sent_at__isnull=False)
        .order_by("-outreach_sent_at")
    )

    if owner == "unassigned":
        qs = qs.filter(assigned_to="")
    elif owner in {"igor", "partner", "shared"}:
        qs = qs.filter(assigned_to=owner)

    if status == "awaiting":
        qs = qs.filter(replied_at__isnull=True)
    elif status == "replied":
        qs = qs.filter(replied_at__isnull=False)
    elif status == "overdue":
        qs = qs.filter(
            replied_at__isnull=True,
            next_followup_at__lt=timezone.now(),
        )

    if search_q:
        qs = qs.filter(
            Q(full_name__icontains=search_q)
            | Q(fund__name__icontains=search_q)
        )

    if days:
        cutoff = timezone.now() - timedelta(days=days)
        qs = qs.filter(outreach_sent_at__gte=cutoff)

    rows = list(qs[:500])

    counters = {
        "total": Person.objects.filter(outreach_sent_at__isnull=False).count(),
        "awaiting": Person.objects.filter(
            outreach_sent_at__isnull=False, replied_at__isnull=True
        ).count(),
        "replied": Person.objects.filter(replied_at__isnull=False).count(),
        "overdue": Person.objects.filter(
            outreach_sent_at__isnull=False,
            replied_at__isnull=True,
            next_followup_at__lt=timezone.now(),
        ).count(),
        "last_7d": Person.objects.filter(
            outreach_sent_at__gte=timezone.now() - timedelta(days=7)
        ).count(),
    }

    context = {
        "title": "Outreach sent log",
        "site_header": "Kubricon Investor CRM",
        "rows": rows,
        "counters": counters,
        "owner": owner,
        "owner_options": [
            ("", "All owners"),
            ("igor", "Igor"),
            ("partner", "Partner"),
            ("shared", "Shared"),
            ("unassigned", "Unassigned"),
        ],
        "status": status,
        "status_options": [
            ("all", "All sent"),
            ("awaiting", "Awaiting reply"),
            ("overdue", "Follow-up overdue"),
            ("replied", "Replied"),
        ],
        "days": days,
        "days_options": [(0, "All time"), (1, "Last 24h"), (7, "Last 7d"), (30, "Last 30d")],
        "search_q": search_q,
        "now": timezone.now(),
        "person_admin_base": reverse("admin:investors_person_changelist"),
    }
    return render(request, "admin/outreach_sent_log.html", context)
