from django.contrib import admin
from django.urls import include, path

from apps.investors.admin_dashboard import outreach_dashboard
from apps.investors.admin_kanban import (
    outreach_kanban,
    outreach_kanban_card,
    outreach_kanban_move,
    outreach_kanban_touch,
)
from apps.investors.admin_sent_log import outreach_sent_log
from apps.investors.admin_worklist import outreach_worklist

urlpatterns = [
    path(
        "admin/outreach/dashboard/",
        outreach_dashboard,
        name="outreach-dashboard",
    ),
    path(
        "admin/outreach/board/",
        outreach_kanban,
        name="outreach-kanban",
    ),
    path(
        "admin/outreach/board/move/",
        outreach_kanban_move,
        name="outreach-kanban-move",
    ),
    path(
        "admin/outreach/board/touch/",
        outreach_kanban_touch,
        name="outreach-kanban-touch",
    ),
    path(
        "admin/outreach/board/card/<int:person_id>/",
        outreach_kanban_card,
        name="outreach-kanban-card",
    ),
    path(
        "admin/outreach/worklist/",
        outreach_worklist,
        name="outreach-worklist",
    ),
    path(
        "admin/outreach/sent/",
        outreach_sent_log,
        name="outreach-sent-log",
    ),
    path("admin/", admin.site.urls),
    path("", include("apps.site.urls")),
]
