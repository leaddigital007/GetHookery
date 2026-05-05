"""
Import an OpenVC export (CSV) using the FundResource column mapping.

Examples:
    python manage.py import_openvc data/openvc-2025-10.csv
    python manage.py import_openvc data/openvc-2025-10.csv --dry-run
    python manage.py import_openvc data/openvc-2025-10.csv --raise-errors
"""
from __future__ import annotations

from pathlib import Path

import tablib
from django.core.management.base import BaseCommand, CommandError

from apps.ingest.services import ingest_run
from apps.investors.resources import FundResource


class Command(BaseCommand):
    help = "Import an OpenVC export (CSV) into the Fund table."

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            help="Path to a CSV file with OpenVC headers.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and validate without writing to the database.",
        )
        parser.add_argument(
            "--raise-errors",
            action="store_true",
            help="Stop on first row error instead of collecting them.",
        )
        parser.add_argument(
            "--source-label",
            default="openvc_2025_10",
            help="Label written into the ImportRun audit row.",
        )

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        dry_run = options["dry_run"]
        raise_errors = options["raise_errors"]
        source_label = options["source_label"]

        with path.open("rb") as f:
            content = f.read()

        try:
            dataset = tablib.Dataset().load(content.decode("utf-8-sig"), format="csv")
        except Exception as exc:  # pragma: no cover - bad input is a hard fail
            raise CommandError(f"Could not parse CSV: {exc}")

        with ingest_run(
            source=source_label,
            command="import_openvc",
            args={"path": str(path), "dry_run": dry_run},
        ) as run:
            run.log(f"Loaded {len(dataset)} rows from {path}")
            run.log(f"Headers: {dataset.headers}")

            resource = FundResource()
            result = resource.import_data(
                dataset,
                dry_run=dry_run,
                raise_errors=raise_errors,
                use_transactions=True,
                collect_failed_rows=True,
            )

            run.run.rows_seen = result.total_rows
            run.created(result.totals.get("new", 0))
            run.updated(result.totals.get("update", 0))
            run.skipped(result.totals.get("skip", 0))
            run.failed(result.totals.get("error", 0) + result.totals.get("invalid", 0))

            run.log(
                "Import done: "
                f"new={result.totals.get('new', 0)} "
                f"updated={result.totals.get('update', 0)} "
                f"skipped={result.totals.get('skip', 0)} "
                f"errors={result.totals.get('error', 0)} "
                f"invalid={result.totals.get('invalid', 0)} "
                f"deleted={result.totals.get('delete', 0)}"
            )

            if result.has_errors() or result.has_validation_errors():
                # Surface the first few problems so we can fix them.
                shown = 0
                for row_errors in result.row_errors():
                    row_index, errors = row_errors
                    for err in errors:
                        run.log(f"  row {row_index} ERROR: {err.error}")
                        shown += 1
                        if shown >= 10:
                            break
                    if shown >= 10:
                        break
                for invalid_row in result.invalid_rows[:10]:
                    run.log(f"  row {invalid_row.number} INVALID: {invalid_row.error_dict}")

            if dry_run:
                run.log("Dry run - all changes rolled back.")
