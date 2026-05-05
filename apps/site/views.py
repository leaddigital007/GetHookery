"""Public-facing endpoints used by the static landing in `public/`."""
from __future__ import annotations

import json
import logging
import re

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.investors.models import ContactSubmission

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
REQUIRED_FIELDS = ("name", "email", "website", "revenue", "message")
MAX_FIELD_LEN = 5000


def _client_ip(request: HttpRequest) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


@csrf_exempt
@require_POST
def contact_submit(request: HttpRequest) -> HttpResponse:
    """Accept the same JSON payload the Node server used and persist it.

    Phase 1 stores the submission only; downstream notifications and reply
    handling are added once the outreach phase begins.
    """
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"ok": False, "error": "Invalid payload"}, status=400)

    cleaned: dict[str, str] = {}
    for field in REQUIRED_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            return JsonResponse(
                {"ok": False, "error": "Missing required fields"}, status=400
            )
        cleaned[field] = value.strip()[:MAX_FIELD_LEN]

    if not EMAIL_RE.match(cleaned["email"]):
        return JsonResponse({"ok": False, "error": "Invalid email"}, status=400)

    submission = ContactSubmission.objects.create(
        name=cleaned["name"],
        email=cleaned["email"],
        website=cleaned["website"],
        revenue=cleaned["revenue"],
        message=cleaned["message"],
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
        ip_address=_client_ip(request),
    )
    logger.info("ContactSubmission stored id=%s email=%s", submission.id, submission.email)
    return JsonResponse({"ok": True})
