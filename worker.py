"""
Long-running background worker for the Kubricon investor CRM.

This process owns all scheduled ingestion jobs. It is intentionally simple:
APScheduler with persistent in-memory schedules, jobs are Django management
commands invoked through `call_command`, and every job's status is recorded
in the `ImportRun` table for observability via the admin UI.

Run locally:

    python worker.py

Run on Heroku (after `heroku ps:scale worker=1`):

    worker: python worker.py

Schedule (UTC):
  * 06:15 daily   -> import_edgar_form_d (last 1 day, max 200)
  * 03:30 Mondays -> import_github_awesome (default lists)

Environment knobs:
  * WORKER_RUN_ON_START=1   run every job once at startup (handy for backfills)
  * WORKER_EDGAR_DAYS=N     override --days passed to import_edgar_form_d
  * WORKER_EDGAR_MAX=N      override --max passed to import_edgar_form_d
  * WORKER_DISABLE_EDGAR=1  skip scheduling the EDGAR job
  * WORKER_DISABLE_GITHUB=1 skip scheduling the GitHub awesome job
"""
from __future__ import annotations

import logging
import os
import signal
import sys
from io import StringIO

import django
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.core.management import call_command  # noqa: E402

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stdout)
logger = logging.getLogger("worker")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid int for %s: %r, using default %s", name, raw, default)
        return default


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _run_command(label: str, command: str, **kwargs) -> None:
    """Wrap call_command with try/except so one bad job does not kill the worker."""
    logger.info("[%s] starting: %s kwargs=%s", label, command, kwargs)
    buf = StringIO()
    try:
        call_command(command, stdout=buf, stderr=buf, **kwargs)
    except Exception:
        logger.exception("[%s] %s FAILED", label, command)
    else:
        logger.info("[%s] %s OK", label, command)
    output = buf.getvalue().strip()
    if output:
        for line in output.splitlines():
            logger.info("[%s]   %s", label, line)


def job_edgar_daily() -> None:
    days = _env_int("WORKER_EDGAR_DAYS", 1)
    max_results = _env_int("WORKER_EDGAR_MAX", 200)
    _run_command(
        "edgar_daily",
        "import_edgar_form_d",
        days=days,
        max=max_results,
    )


def job_github_weekly() -> None:
    _run_command("github_weekly", "import_github_awesome")


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="UTC")

    if _env_truthy("WORKER_DISABLE_EDGAR"):
        logger.warning("EDGAR job disabled via WORKER_DISABLE_EDGAR")
    else:
        scheduler.add_job(
            job_edgar_daily,
            CronTrigger(hour=6, minute=15),
            id="edgar_daily",
            name="EDGAR Form D daily delta",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )

    if _env_truthy("WORKER_DISABLE_GITHUB"):
        logger.warning("GitHub awesome job disabled via WORKER_DISABLE_GITHUB")
    else:
        scheduler.add_job(
            job_github_weekly,
            CronTrigger(day_of_week="mon", hour=3, minute=30),
            id="github_weekly",
            name="GitHub awesome lists weekly refresh",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600 * 6,
        )

    return scheduler


def install_shutdown_handlers(scheduler: BlockingScheduler) -> None:
    def _shutdown(signum, _frame):
        logger.info("Received signal %s, shutting down scheduler", signum)
        scheduler.shutdown(wait=False)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _shutdown)


def main() -> None:
    scheduler = build_scheduler()
    install_shutdown_handlers(scheduler)

    if _env_truthy("WORKER_RUN_ON_START"):
        logger.info("WORKER_RUN_ON_START=1, executing all jobs once before scheduling")
        if not _env_truthy("WORKER_DISABLE_GITHUB"):
            job_github_weekly()
        if not _env_truthy("WORKER_DISABLE_EDGAR"):
            job_edgar_daily()

    logger.info("Worker starting. Jobs registered:")
    for job in scheduler.get_jobs():
        next_run = getattr(job, "next_run_time", None) or "(scheduled on start)"
        logger.info("  %s -> %s (next run: %s)", job.id, job.name, next_run)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker exiting")


if __name__ == "__main__":
    main()
