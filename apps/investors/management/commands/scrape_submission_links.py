"""
Scrape fund websites looking for a pitch / submission URL or contact email.

Pure HTTP + regex - no LLM, no cost, idempotent. Visits the fund's
homepage, extracts every <a href> and every mailto:, then picks the
strongest signal by priority:

  1. submission form: /pitch, /submit-a-pitch, /submit, /apply,
     /partner-with-us, /connect, /founders, /work-with-us
  2. contact form:    /contact, /get-in-touch
  3. mailto:          first published email

If a homepage redirects, we follow up to 3 hops. JS-only sites are out
of scope - we'll fall back to the LLM later for those.

Examples:
    python manage.py scrape_submission_links --tiers S --dry-run
    python manage.py scrape_submission_links --tiers S,1 --apply
    python manage.py scrape_submission_links --tiers S --apply --only-missing
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import requests
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.db.models import Q

from apps.ingest.services import ingest_run
from apps.investors.models import Fund

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

CONNECT_TIMEOUT = 5
READ_TIMEOUT = 10

# Highest-priority patterns first - explicit pitch invitations.
PITCH_PATTERNS = [
    re.compile(r"/(submit-a-pitch|submit-pitch|pitch-?us|pitch[/?]|pitch$)", re.I),
    re.compile(r"/(submit|apply)([/?].*)?$", re.I),
    re.compile(r"/(partner-with-us|work-with-us|connect|founders?)([/?].*)?$", re.I),
]

# Lower-priority - generic contact pages we accept only if no pitch URL.
CONTACT_PATTERNS = [
    re.compile(r"/(contact|get-in-touch|reach-us|contact-us)([/?].*)?$", re.I),
]

EMAIL_RE = re.compile(r"mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)

# Skip emails that are obvious junk / not pitch-relevant.
EMAIL_BLOCKLIST = re.compile(
    r"(privacy|press|media|legal|gdpr|hr|jobs|careers|recruit|noreply|"
    r"no-reply|support@|webmaster|admin@|abuse|dpo)",
    re.I,
)

NOTE_PREFIX = "Scraped-submit:"


def _replace_note(internal_notes: str, new_note: str) -> str:
    text = internal_notes or ""
    lines = [
        ln
        for ln in text.splitlines()
        if not ln.strip().startswith(NOTE_PREFIX)
    ]
    lines.append(new_note)
    return "\n".join(line for line in lines if line.strip())[:4000]


def _normalise_url(base: str, href: str) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("javascript:", "#", "tel:", "mailto:")):
        return None
    try:
        return urljoin(base, href)
    except Exception:
        return None


def _same_domain(a: str, b: str) -> bool:
    try:
        return urlparse(a).netloc.lower() == urlparse(b).netloc.lower()
    except Exception:
        return False


def _find_in_html(*, html: str, base_url: str) -> tuple[str, str, str]:
    """Return (best_url, best_email, kind) extracted from one HTML page."""
    hrefs = HREF_RE.findall(html or "")
    base_host_match = (
        re.match(r"^https?://[^/]+", base_url) or [""]
    )

    pitch_url = ""
    contact_url = ""
    for raw in hrefs:
        url = _normalise_url(base_url, raw)
        if not url:
            continue
        if not _same_domain(base_url, url):
            continue
        path_q = url[len(base_host_match[0]) :] if base_url else url
        if not pitch_url:
            for pat in PITCH_PATTERNS:
                if pat.search(path_q):
                    pitch_url = url
                    break
        if not contact_url:
            for pat in CONTACT_PATTERNS:
                if pat.search(path_q):
                    contact_url = url
                    break
        if pitch_url and contact_url:
            break

    emails = []
    for m in EMAIL_RE.finditer(html or ""):
        addr = m.group(1).strip()
        if EMAIL_BLOCKLIST.search(addr):
            continue
        emails.append(addr)
    best_email = emails[0] if emails else ""

    if pitch_url:
        return pitch_url, best_email, "pitch"
    if contact_url:
        return contact_url, best_email, "contact"
    return "", best_email, "none"


class Command(BaseCommand):
    help = "Scrape fund websites for submission URL + contact email."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--tiers", type=str, default=None)
        parser.add_argument("--only-missing", action="store_true")
        parser.add_argument(
            "--concurrency",
            type=int,
            default=8,
            help="Parallel HTTP workers (default 8).",
        )
        parser.add_argument("--quiet", action="store_true")

    def handle(self, *args, **options):
        limit = options.get("limit")
        dry_run = options.get("dry_run", False) or not options.get("apply", False)
        tiers_arg = options.get("tiers")
        only_missing = options.get("only_missing", False)
        concurrency = max(1, int(options.get("concurrency") or 1))
        quiet = options.get("quiet", False)

        qs = Fund.objects.exclude(website="")
        if tiers_arg:
            wanted = [t.strip() for t in tiers_arg.split(",") if t.strip()]
            qs = qs.filter(tier__in=wanted)
        if only_missing:
            qs = qs.filter(Q(submission_url="") & Q(contact_email=""))
        qs = qs.order_by("tier", "-check_max_usd", "name")
        if limit:
            qs = qs[:limit]

        funds = list(qs)

        def _scrape_one(fund) -> tuple:
            """Worker: returns (fund_id, kind, url, email, error_str)."""
            close_old_connections()
            sess = requests.Session()
            sess.headers.update({"User-Agent": USER_AGENT})
            website = fund.website.strip()
            if not website.startswith(("http://", "https://")):
                website = "https://" + website
            try:
                resp = sess.get(
                    website,
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                    allow_redirects=True,
                )
                if resp.status_code >= 400:
                    return (fund.id, "none", "", "", f"HTTP {resp.status_code}")
                best_url, best_email, kind = _find_in_html(
                    html=resp.text or "", base_url=resp.url
                )
                return (fund.id, kind, best_url, best_email, "")
            except Exception as exc:
                return (fund.id, "none", "", "", str(exc)[:120])
            finally:
                try:
                    sess.close()
                except Exception:
                    pass

        with ingest_run(
            source="scrape_submission_links",
            command="scrape_submission_links",
            args={
                "limit": limit,
                "dry_run": dry_run,
                "tiers": tiers_arg,
                "only_missing": only_missing,
                "concurrency": concurrency,
            },
        ) as run:
            run.log(
                f"Scraping {len(funds)} websites (dry_run={dry_run}, concurrency={concurrency})."
            )

            total_pitch = 0
            total_contact = 0
            total_email = 0
            total_failed = 0
            total_updated = 0
            done = 0
            started = time.monotonic()

            funds_by_id = {f.id: f for f in funds}

            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                futures = {ex.submit(_scrape_one, f): f.id for f in funds}
                for fut in as_completed(futures):
                    run.saw()
                    done += 1
                    fund_id, kind, best_url, best_email, error = fut.result()
                    fund = funds_by_id[fund_id]

                    if error:
                        total_failed += 1
                        run.failed()
                        if not quiet:
                            self.stdout.write(
                                f"  [{done}/{len(funds)}] {fund.name[:32]:32} "
                                f"FAIL: {error}"
                            )
                        continue

                    if kind == "pitch":
                        total_pitch += 1
                    elif kind == "contact":
                        total_contact += 1
                    if best_email:
                        total_email += 1

                    if not quiet:
                        elapsed = time.monotonic() - started
                        rate = done / elapsed if elapsed > 0 else 0.0
                        self.stdout.write(
                            f"  [{done}/{len(funds)} {rate:.1f} rps] "
                            f"{fund.name[:30]:30} {kind:7} "
                            f"url={best_url[:50]:50} email={best_email[:28]}"
                        )

                    if dry_run:
                        continue
                    if not best_url and not best_email:
                        continue

                    update_fields = ["internal_notes", "updated_at"]
                    if best_url and best_url != fund.submission_url:
                        fund.submission_url = best_url
                        update_fields.append("submission_url")
                    if best_email and best_email != fund.contact_email:
                        fund.contact_email = best_email
                        update_fields.append("contact_email")
                    fund.internal_notes = _replace_note(
                        fund.internal_notes,
                        f"{NOTE_PREFIX} kind={kind}"
                        + (f" url={best_url}" if best_url else "")
                        + (f" email={best_email}" if best_email else ""),
                    )
                    fund.save(update_fields=update_fields)
                    total_updated += 1
                    run.updated()

            run.log(
                f"Done. updated={total_updated} pitch_url={total_pitch} "
                f"contact_url={total_contact} email={total_email} failed={total_failed}"
            )

            if dry_run:
                self.stdout.write(
                    self.style.WARNING("Dry run - no DB writes. Re-run with --apply.")
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Updated {total_updated} funds; "
                        f"pitch={total_pitch} contact={total_contact} email={total_email} "
                        f"failed={total_failed}."
                    )
                )
