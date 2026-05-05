"""
Phase 2 of the NFX Signal import: scrape each Signal profile page and
enrich Person rows with twitter_handle, linkedin_url, role, and
location.

NFX renders pages via React + Apollo. The full investor object is
serialised into `window.__APOLLO_STATE__` as a JSON cache; we parse
that directly instead of walking the DOM. Pagination (>8 rounds) is
NOT followed - the local cache contains only what's visible in the
hero section.

Examples:
    python manage.py scrape_nfx_signal --limit 5 --dry-run
    python manage.py scrape_nfx_signal --apply --rps 1.0
    python manage.py scrape_nfx_signal --apply --only-missing-twitter
"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlparse

import requests
from django.core.management.base import BaseCommand

from apps.ingest.services import ingest_run, upsert_external_ref
from apps.investors.models import Person

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)
DEFAULT_RPS = 1.0
NFX_URL_RE = re.compile(r"NFX Signal:\s*(https?://signal\.nfx\.com/[^\s\n]+)")
APOLLO_RE = re.compile(
    r"window\.__APOLLO_STATE__\s*=\s*(\{.+?\})\s*</script>", re.DOTALL
)
TWITTER_HANDLE_RE = re.compile(
    r"https?://(?:www\.)?(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,30})/?"
)


def _extract_apollo_state(html: str) -> dict | None:
    m = APOLLO_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _find_first(data: dict, prefix: str) -> dict | None:
    for k, v in data.items():
        if k.startswith(prefix) and isinstance(v, dict):
            return v
    return None


def _twitter_handle_from(url: str | None) -> str:
    if not url:
        return ""
    m = TWITTER_HANDLE_RE.search(url)
    return m.group(1) if m else ""


def _slug_from_nfx_url(url: str) -> str:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    return parts[-1] if parts else ""


class Command(BaseCommand):
    help = "Enrich Person rows by scraping their NFX Signal profile pages."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--only-missing-twitter",
            action="store_true",
            help="Skip persons that already have a twitter_handle.",
        )
        parser.add_argument(
            "--rps",
            type=float,
            default=DEFAULT_RPS,
            help=f"Requests per second cap (default {DEFAULT_RPS}).",
        )
        parser.add_argument("--quiet", action="store_true")

    def handle(self, *args, **options):
        limit = options.get("limit")
        dry_run = options.get("dry_run", False) or not options.get("apply", False)
        only_missing = options.get("only_missing_twitter", False)
        rps = max(0.1, float(options.get("rps") or DEFAULT_RPS))
        delay = 1.0 / rps
        quiet = options.get("quiet", False)

        qs = Person.objects.filter(internal_notes__icontains="NFX Signal:")
        if only_missing:
            qs = qs.filter(twitter_handle="")
        qs = qs.order_by("full_name")
        if limit:
            qs = qs[:limit]
        people = list(qs)

        with ingest_run(
            source="nfx_signal_scrape",
            command="scrape_nfx_signal",
            args={
                "limit": limit,
                "dry_run": dry_run,
                "rps": rps,
                "only_missing": only_missing,
            },
        ) as run:
            run.log(
                f"Scraping {len(people)} NFX profiles (rps={rps}, dry_run={dry_run})."
            )

            session = requests.Session()
            session.headers["User-Agent"] = DEFAULT_UA

            updated = 0
            failed = 0
            twitter_added = 0
            linkedin_added = 0
            role_added = 0

            try:
                for i, person in enumerate(people, 1):
                    run.saw()
                    m = NFX_URL_RE.search(person.internal_notes or "")
                    if not m:
                        continue
                    nfx_url = m.group(1).rstrip(".,;")
                    slug = _slug_from_nfx_url(nfx_url)

                    if i > 1:
                        time.sleep(delay)

                    try:
                        resp = session.get(nfx_url, timeout=15)
                    except requests.RequestException as e:
                        failed += 1
                        run.failed()
                        run.log(f"  {person.full_name[:30]:30} HTTP error: {e!r}")
                        continue
                    if resp.status_code != 200:
                        failed += 1
                        run.failed()
                        run.log(
                            f"  {person.full_name[:30]:30} HTTP {resp.status_code}"
                        )
                        continue

                    state = _extract_apollo_state(resp.text)
                    if not state:
                        failed += 1
                        run.failed()
                        run.log(
                            f"  {person.full_name[:30]:30} no Apollo state"
                        )
                        continue

                    public_person = _find_first(state, "PublicPerson:")
                    profile = _find_first(state, "PublicInvestorProfile:")

                    twitter_url = (public_person or {}).get("twitter_url") or ""
                    linkedin_url = (public_person or {}).get("linkedin_url") or ""
                    headline = (profile or {}).get("headline") or ""
                    twitter_handle = _twitter_handle_from(twitter_url)

                    updates: dict[str, str] = {}
                    if twitter_handle and not person.twitter_handle:
                        updates["twitter_handle"] = twitter_handle[:80]
                    if linkedin_url and not person.linkedin_url:
                        updates["linkedin_url"] = linkedin_url[:200]
                    if headline and not person.role:
                        updates["role"] = headline[:120]

                    if not quiet:
                        deltas = []
                        if "twitter_handle" in updates:
                            deltas.append(f"@{updates['twitter_handle']}")
                        if "linkedin_url" in updates:
                            deltas.append("LI")
                        if "role" in updates:
                            deltas.append(f"role='{updates['role'][:40]}'")
                        marker = "+" if deltas else "."
                        self.stdout.write(
                            f"  {marker} [{i}/{len(people)}] {person.full_name[:28]:28} "
                            f"-> {', '.join(deltas) or '(no change)'}"
                        )

                    if updates and not dry_run:
                        for k, v in updates.items():
                            setattr(person, k, v)
                        person.save(
                            update_fields=list(updates.keys()) + ["updated_at"]
                        )
                        updated += 1
                        twitter_added += int("twitter_handle" in updates)
                        linkedin_added += int("linkedin_url" in updates)
                        role_added += int("role" in updates)
                        run.updated()

                    if not dry_run and slug:
                        upsert_external_ref(
                            source="signal_nfx",
                            external_id=slug,
                            target=person,
                            payload={
                                "nfx_url": nfx_url,
                                "twitter_url": twitter_url,
                                "linkedin_url": linkedin_url,
                                "headline": headline,
                            },
                        )
            except KeyboardInterrupt:
                run.log("Interrupted; partial results above are persisted.")

            run.log(
                f"Done. updated={updated} failed={failed} "
                f"twitter+={twitter_added} linkedin+={linkedin_added} role+={role_added}"
            )
            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        f"Dry run: would update {updated}/{len(people)}; "
                        "re-run with --apply."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Scraped {len(people)} profiles, updated {updated}. "
                        f"+{twitter_added} twitter, +{linkedin_added} LinkedIn, "
                        f"+{role_added} role; {failed} failed."
                    )
                )
