from django.contrib import admin
from django.urls import include, path

from apps.investors.admin_dashboard import outreach_dashboard

urlpatterns = [
    path(
        "admin/outreach/dashboard/",
        outreach_dashboard,
        name="outreach-dashboard",
    ),
    path("admin/", admin.site.urls),
    path("", include("apps.site.urls")),
]
