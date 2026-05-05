"""
Phase 2 of the NFX Signal import: scrape each Signal profile page and
enrich Person rows with twitter_handle, linkedin_url, role, and
location.

NFX blocks Heroku-class IPs (403), so the command supports a split
workflow:

    1. Extract (run locally on a residential IP):
        python manage.py scrape_nfx_signal \\
            --extract-to data/nfx-enrichment.jsonl --rps 1.0

    2. Apply (run on Heroku or locally against prod DB):
        python manage.py scrape_nfx_signal \\
            --apply-from data/nfx-enrichment.jsonl --apply

The JSONL file is the only artifact crossing the boundary, so we
never need a tunnel into Heroku Postgres. Each line is one Person
slug -> {twitter_url, linkedin_url, headline, ...} payload extracted
from window.__APOLLO_STATE__.

The legacy "scrape-and-write-in-one-pass" mode is still supported
when running on a residential IP (`--apply` without `--apply-from`).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from django.core.management.base import BaseCommand, CommandError

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


def _scrape_one(session: requests.Session, nfx_url: str) -> tuple[dict | None, str]:
    """Fetch one profile, return (payload, error). Either is None on success."""
    try:
        resp = session.get(nfx_url, timeout=15)
    except requests.RequestException as e:
        return None, f"http error: {e!r}"
    if resp.status_code != 200:
        return None, f"http {resp.status_code}"

    state = _extract_apollo_state(resp.text)
    if not state:
        return None, "no apollo state"

    public_person = _find_first(state, "PublicPerson:")
    profile = _find_first(state, "PublicInvestorProfile:")
    public_firm = _find_first(state, "PublicFirm:")

    return {
        "twitter_url": (public_person or {}).get("twitter_url") or "",
        "linkedin_url": (public_person or {}).get("linkedin_url") or "",
        "crunchbase_url": (public_person or {}).get("crunchbase_url") or "",
        "angellist_url": (public_person or {}).get("angellist_url") or "",
        "headline": (profile or {}).get("headline") or "",
        "previous_position": (profile or {}).get("previous_position") or "",
        "previous_firm": (profile or {}).get("previous_firm") or "",
        "min_investment": (profile or {}).get("min_investment"),
        "max_investment": (profile or {}).get("max_investment"),
        "target_investment": (profile or {}).get("target_investment"),
        "vote_count": (profile or {}).get("vote_count"),
        "firm_name": (public_firm or {}).get("name") or "",
        "firm_slug": (public_firm or {}).get("slug") or "",
    }, ""


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
        parser.add_argument(
            "--extract-to",
            type=str,
            default=None,
            help=(
                "Run extraction only: scrape NFX, write JSONL to this file, "
                "do not touch the DB. Use on a residential IP."
            ),
        )
        parser.add_argument(
            "--extract-from-csv",
            type=str,
            default=None,
            help=(
                "When extracting, read URL list from this CSV instead of the "
                "DB (so we don't need DB access on the residential machine). "
                "CSV must have an 'Investor Full Name' and "
                "'Signal Profile link' columns."
            ),
        )
        parser.add_argument(
            "--apply-from",
            type=str,
            default=None,
            help=(
                "Skip HTTP entirely; read enrichment JSONL produced by "
                "--extract-to and apply to DB."
            ),
        )

    def handle(self, *args, **options):
        limit = options.get("limit")
        dry_run = options.get("dry_run", False) or not options.get("apply", False)
        only_missing = options.get("only_missing_twitter", False)
        rps = max(0.1, float(options.get("rps") or DEFAULT_RPS))
        delay = 1.0 / rps
        quiet = options.get("quiet", False)
        extract_to = options.get("extract_to")
        extract_from_csv = options.get("extract_from_csv")
        apply_from = options.get("apply_from")

        if extract_to and apply_from:
            raise CommandError("--extract-to and --apply-from are mutually exclusive.")

        if apply_from:
            return self._apply_from_jsonl(
                Path(apply_from), dry_run=dry_run, quiet=quiet
            )

        if extract_to and extract_from_csv:
            return self._extract_from_csv_to_jsonl(
                csv_path=Path(extract_from_csv),
                out_path=Path(extract_to),
                rps=rps,
                delay=delay,
                quiet=quiet,
                limit=limit,
            )

        qs = Person.objects.filter(internal_notes__icontains="NFX Signal:")
        if only_missing:
            qs = qs.filter(twitter_handle="")
        qs = qs.order_by("full_name")
        if limit:
            qs = qs[:limit]
        people = list(qs)

        if extract_to:
            return self._extract_to_jsonl(
                people, Path(extract_to), rps=rps, delay=delay, quiet=quiet
            )

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
                f"Scraping {len(people)} NFX profiles in-process "
                f"(rps={rps}, dry_run={dry_run})."
            )

            session = requests.Session()
            session.headers["User-Agent"] = DEFAULT_UA

            stats = {
                "updated": 0,
                "failed": 0,
                "twitter_added": 0,
                "linkedin_added": 0,
                "role_added": 0,
            }

            try:
                for i, person in enumerate(people, 1):
                    run.saw()
                    m = NFX_URL_RE.search(person.internal_notes or "")
                    if not m:
                        continue
                    nfx_url = m.group(1).rstrip(".,;")

                    if i > 1:
                        time.sleep(delay)

                    payload, err = _scrape_one(session, nfx_url)
                    if payload is None:
                        stats["failed"] += 1
                        run.failed()
                        run.log(f"  {person.full_name[:30]:30} {err}")
                        continue

                    self._apply_payload_to_person(
                        person, nfx_url, payload, stats, dry_run, quiet, i, len(people)
                    )
                    if not dry_run:
                        run.updated()
            except KeyboardInterrupt:
                run.log("Interrupted; partial results above are persisted.")

            self._final_log(run, stats, len(people), dry_run, mode="in-process")

    def _apply_payload_to_person(
        self,
        person: Person,
        nfx_url: str,
        payload: dict,
        stats: dict,
        dry_run: bool,
        quiet: bool,
        idx: int | None,
        total: int | None,
    ) -> None:
        twitter_handle = _twitter_handle_from(payload.get("twitter_url") or "")
        linkedin_url = (payload.get("linkedin_url") or "").strip()
        headline = (payload.get("headline") or "").strip()

        updates: dict[str, str] = {}
        if twitter_handle and not person.twitter_handle:
            updates["twitter_handle"] = twitter_handle[:80]
        if linkedin_url and not person.linkedin_url:
            updates["linkedin_url"] = linkedin_url[:200]
        if headline and not person.role:
            updates["role"] = headline[:120]

        if not quiet:
            deltas: list[str] = []
            if "twitter_handle" in updates:
                deltas.append(f"@{updates['twitter_handle']}")
            if "linkedin_url" in updates:
                deltas.append("LI")
            if "role" in updates:
                deltas.append(f"role='{updates['role'][:40]}'")
            marker = "+" if deltas else "."
            prefix = f"[{idx}/{total}] " if idx and total else ""
            self.stdout.write(
                f"  {marker} {prefix}{person.full_name[:28]:28} "
                f"-> {', '.join(deltas) or '(no change)'}"
            )

        if updates and not dry_run:
            for k, v in updates.items():
                setattr(person, k, v)
            person.save(update_fields=list(updates.keys()) + ["updated_at"])
            stats["updated"] += 1
            stats["twitter_added"] += int("twitter_handle" in updates)
            stats["linkedin_added"] += int("linkedin_url" in updates)
            stats["role_added"] += int("role" in updates)

        if not dry_run:
            slug = _slug_from_nfx_url(nfx_url)
            if slug:
                upsert_external_ref(
                    source="signal_nfx",
                    external_id=slug,
                    target=person,
                    payload={"nfx_url": nfx_url, **payload},
                )

    def _extract_to_jsonl(
        self,
        people: list[Person],
        out_path: Path,
        *,
        rps: float,
        delay: float,
        quiet: bool,
    ) -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        session = requests.Session()
        session.headers["User-Agent"] = DEFAULT_UA

        ok = 0
        failed = 0

        with ingest_run(
            source="nfx_signal_extract",
            command="scrape_nfx_signal --extract-to",
            args={"out": str(out_path), "rps": rps, "people": len(people)},
        ) as run:
            run.log(
                f"Extracting {len(people)} NFX profiles to {out_path} "
                f"at {rps} rps."
            )
            with out_path.open("w", encoding="utf-8") as fh:
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

                        payload, err = _scrape_one(session, nfx_url)
                        if payload is None:
                            failed += 1
                            run.failed()
                            run.log(
                                f"  [{i}/{len(people)}] {person.full_name[:28]:28} "
                                f"FAIL: {err}"
                            )
                            continue

                        record = {
                            "person_id": person.id,
                            "person_full_name": person.full_name,
                            "nfx_url": nfx_url,
                            "nfx_slug": slug,
                            **payload,
                        }
                        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                        fh.flush()
                        ok += 1
                        if not quiet:
                            twitter = _twitter_handle_from(payload["twitter_url"])
                            self.stdout.write(
                                f"  + [{i}/{len(people)}] {person.full_name[:28]:28} "
                                f"@{twitter or '-'}  "
                                f"LI={'yes' if payload['linkedin_url'] else 'no'}  "
                                f"role={payload['headline'][:40]!r}"
                            )
                except KeyboardInterrupt:
                    run.log("Interrupted; partial JSONL written.")

            run.log(f"Done. ok={ok} failed={failed} out={out_path}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {ok} payloads to {out_path} ({failed} failed)."
            )
        )

    def _extract_from_csv_to_jsonl(
        self,
        *,
        csv_path: Path,
        out_path: Path,
        rps: float,
        delay: float,
        quiet: bool,
        limit: int | None,
    ) -> None:
        """Pure-CSV extract mode: skip the DB lookup entirely."""
        import csv as _csv

        if not csv_path.exists():
            raise CommandError(f"CSV not found: {csv_path}")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        session = requests.Session()
        session.headers["User-Agent"] = DEFAULT_UA

        with csv_path.open("r", encoding="utf-8-sig", newline="") as cfh:
            rows = list(_csv.DictReader(cfh))
        if limit:
            rows = rows[:limit]

        ok = 0
        failed = 0
        self.stdout.write(
            f"Extracting {len(rows)} profiles from {csv_path} -> {out_path} "
            f"at {rps} rps."
        )
        with out_path.open("w", encoding="utf-8") as fh:
            try:
                for i, row in enumerate(rows, 1):
                    full_name = (row.get("Investor Full Name") or "").strip()
                    nfx_url = (row.get("Signal Profile link") or "").strip()
                    if not nfx_url or not full_name:
                        continue
                    slug = _slug_from_nfx_url(nfx_url)

                    if i > 1:
                        time.sleep(delay)

                    payload, err = _scrape_one(session, nfx_url)
                    if payload is None:
                        failed += 1
                        if not quiet:
                            self.stdout.write(
                                f"  ! [{i}/{len(rows)}] {full_name[:28]:28} FAIL: {err}"
                            )
                        continue

                    record = {
                        "person_full_name": full_name,
                        "firm_name_csv": (row.get("Firm Name") or "").strip(),
                        "nfx_url": nfx_url,
                        "nfx_slug": slug,
                        **payload,
                    }
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                    fh.flush()
                    ok += 1
                    if not quiet:
                        twitter = _twitter_handle_from(payload["twitter_url"])
                        self.stdout.write(
                            f"  + [{i}/{len(rows)}] {full_name[:28]:28} "
                            f"@{twitter or '-':<18} "
                            f"LI={'yes' if payload['linkedin_url'] else 'no '}  "
                            f"role={payload['headline'][:40]!r}"
                        )
            except KeyboardInterrupt:
                self.stdout.write(
                    self.style.WARNING("Interrupted; partial JSONL written.")
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {ok} payloads to {out_path} ({failed} failed)."
            )
        )

    def _apply_from_jsonl(
        self, in_path: Path, *, dry_run: bool, quiet: bool
    ) -> None:
        if not in_path.exists():
            raise CommandError(f"JSONL not found: {in_path}")

        stats = {
            "updated": 0,
            "failed": 0,
            "twitter_added": 0,
            "linkedin_added": 0,
            "role_added": 0,
        }
        seen = 0

        with ingest_run(
            source="nfx_signal_apply",
            command="scrape_nfx_signal --apply-from",
            args={"in": str(in_path), "dry_run": dry_run},
        ) as run:
            run.log(f"Applying enrichment from {in_path} (dry_run={dry_run}).")
            with in_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        run.failed()
                        continue

                    seen += 1
                    run.saw()
                    person = (
                        Person.objects.filter(id=record.get("person_id")).first()
                        or Person.objects.filter(
                            full_name__iexact=record.get("person_full_name") or ""
                        ).first()
                    )
                    if person is None:
                        stats["failed"] += 1
                        run.failed()
                        run.log(
                            f"  miss: {record.get('person_full_name')!r} "
                            f"id={record.get('person_id')}"
                        )
                        continue

                    self._apply_payload_to_person(
                        person,
                        record.get("nfx_url") or "",
                        record,
                        stats,
                        dry_run,
                        quiet,
                        idx=None,
                        total=None,
                    )
                    if not dry_run:
                        run.updated()

            self._final_log(run, stats, seen, dry_run, mode="apply-from-jsonl")

    def _final_log(
        self,
        run,
        stats: dict,
        total: int,
        dry_run: bool,
        *,
        mode: str,
    ) -> None:
        run.log(
            f"Done [{mode}]. seen={total} updated={stats['updated']} "
            f"failed={stats['failed']} "
            f"twitter+={stats['twitter_added']} "
            f"linkedin+={stats['linkedin_added']} "
            f"role+={stats['role_added']}"
        )
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: would update {stats['updated']}/{total}; "
                    "re-run with --apply."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Processed {total} profiles, updated {stats['updated']}. "
                    f"+{stats['twitter_added']} twitter, "
                    f"+{stats['linkedin_added']} LinkedIn, "
                    f"+{stats['role_added']} role; "
                    f"{stats['failed']} failed."
                )
            )
