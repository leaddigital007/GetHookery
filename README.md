# GetHookery / Kubricon — Investor CRM

A single Heroku app that serves two surfaces:

- **Public landing** at `/` — the existing static site in [`public/`](public/),
  unchanged for visitors.
- **Closed admin** at `/admin/` — Django Admin powered investor CRM used to
  collect, enrich and follow up on funds, partners, angels, comparable
  companies and their deals while we run the $2M pre-seed/seed round.

Email outreach (sending, open/click tracking, sequences) is intentionally
**out of scope for this phase**. The app stores everything we need to start
the outreach engine in a later phase without schema migrations.

## Stack

- Python 3.13, Django 5.2 LTS
- Gunicorn, WhiteNoise (also serves the public landing at root URLs)
- Postgres on Heroku via the Stackhero add-on
- `django-import-export` for CSV import/export inside the admin
- `dj-database-url` + `django-environ` for 12-factor configuration

## Production

- App: `gethookery-agency`
- URL: <https://gethookery-agency-3cc368fea69d.herokuapp.com/>
- Admin: <https://gethookery-agency-3cc368fea69d.herokuapp.com/admin/>
- Auto-deploy: every `git push heroku main` runs the release phase
  (`migrate` + `collectstatic`) and then restarts the web dyno.

### Required Heroku config vars

| Var | Purpose |
| --- | --- |
| `DJANGO_SECRET_KEY` | Django cryptographic key. Rotate via `heroku config:set`. |
| `DJANGO_DEBUG` | Must be `False` in production. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated. Includes the Heroku hostname and any custom domain. |
| `STACKHERO_POSTGRESQL_DATABASE_URL` | Auto-set by the Stackhero add-on. Settings fall back to it when `DATABASE_URL` is unset. |

### First-time deploy

```bash
heroku buildpacks:set heroku/python -a gethookery-agency
heroku config:set -a gethookery-agency \
  DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" \
  DJANGO_DEBUG=False \
  DJANGO_ALLOWED_HOSTS=gethookery-agency-3cc368fea69d.herokuapp.com,.herokuapp.com
git push heroku main
```

The release phase applies migrations automatically.

### Creating / rotating the admin user

The current bootstrap superuser is `admin` (set with a temporary password
during initial deploy — change it immediately on first login at
`/admin/password/`).

To create a brand-new superuser:

```bash
heroku run -a gethookery-agency python manage.py createsuperuser
```

To reset the password of an existing user without an email round-trip:

```bash
heroku run -a gethookery-agency python manage.py changepassword admin
```

## Local development

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env: set DJANGO_SECRET_KEY and DATABASE_URL (or leave the SQLite default)

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open <http://127.0.0.1:8000/> for the landing and
<http://127.0.0.1:8000/admin/> for the CRM.

## Project layout

```
GetHookery/
  public/                   public landing assets, served at root URLs
                            via WhiteNoise (WHITENOISE_ROOT)
  manage.py
  Procfile                  release: migrate + collectstatic; web: gunicorn
  requirements.txt
  .python-version           Python 3.13 for Heroku
  config/                   Django project (settings, urls, wsgi, asgi)
  apps/
    investors/              domain models, admin, CSV resources
    ingest/                 ingestion pipelines (Phase 2): models, sources,
                            management commands, services
    site/                   /api/contact view used by the public landing
  worker.py                 long-running APScheduler process (worker dyno)
```

## Domain model

| Model | What it stores |
| --- | --- |
| `Tag` | Reusable thesis/category tags. |
| `Fund` | VC fund, family office, syndicate. Tier S/1/2/Watch, check range, thesis, source. |
| `Person` | Partner, principal, or solo angel (`fund=null`). Pipeline stage and warmth. |
| `Company` | Comparable / portfolio company we use as a thesis-fit signal. |
| `Deal` | One funding round announcement for a `Company`. |
| `Investment` | Through-table that links a `Fund` to a `Deal` it joined (lead flag included). |
| `Note` | Free-form note attachable to any of the four entities above. |
| `Task` | TODO/follow-up tied optionally to a fund or a person. |
| `ContactSubmission` | Submissions from the public landing's contact form. |

## CSV import workflow

The Fund / Person / Company / Deal / Investment / Tag admin pages all expose
**Import** and **Export** buttons (top right) backed by
`django-import-export`.

The Fund importer also accepts the column headers produced by **OpenVC's**
"Export to CSV":

| OpenVC column | Mapped to |
| --- | --- |
| `Investor name`, `Investor`, `Fund name` | `name` |
| `Website`, `URL` | `website` |
| `HQ Country`, `Country` | `hq_country` |
| `HQ City`, `City` | `hq_city` |
| `AUM` | `aum_text` |
| `Min Check`, `Min Check Size` | `check_min_usd` (handles `$`, commas, `k`/`M`) |
| `Max Check`, `Max Check Size` | `check_max_usd` |
| `Stages`, `Stage` | `stages` (comma- or semicolon-separated) |
| `Thesis`, `Investment Thesis` | `thesis_summary` |
| `Notable Investments`, `Notable Invest` | `portfolio_notes` |

If `slug` is missing it is generated from `name` automatically.

For Person imports, the `fund` column is matched against `Fund.slug`. Solo
angels can be imported with an empty `fund` value.

## Phase 2 — ingestion pipelines

The `apps/ingest` app and `worker.py` form the data-collection layer. Three
small admin-only tables back every pipeline:

- **`ExternalRef`** — `(source, external_id)` -> any internal record. Powers
  idempotent upserts so a job can be run any number of times.
- **`ImportRun`** — audit row written by every command run (counters,
  status, log tail). Visible in admin under *Ingest -> Import runs*.
- **`Signal`** — triage queue for ambiguous data (unmatched filers, deal
  hints from RSS, social posts). Promoted to canonical Fund/Person/Company
  records by an operator from the admin.

### Available management commands

```bash
# Pull recent SEC EDGAR Form D filings, create Companies + Deals + Signals
python manage.py import_edgar_form_d --days 1 --max 200

# Re-run last 30 days backfill (used once on first deploy)
python manage.py import_edgar_form_d --days 30 --max 1000

# Pull curated awesome-vc style fund lists from GitHub
python manage.py import_github_awesome
```

Both commands are idempotent: a second run skips filings/funds already
seen via `ExternalRef`.

### Worker dyno (`worker.py`)

`worker.py` runs APScheduler in the foreground and triggers management
commands on a UTC schedule:

| Job | Cron | What it does |
| --- | --- | --- |
| `edgar_daily` | 06:15 UTC daily | `import_edgar_form_d --days 1 --max 200` |
| `github_weekly` | 03:30 UTC Mondays | `import_github_awesome` (default lists) |

Heroku process is declared in `Procfile` but **scaled to zero by default**
to preserve the shared Eco dyno-hour quota. Turn it on with:

```bash
# Start the worker dyno
heroku ps:scale worker=1 -a gethookery-agency

# Stop it (e.g. before backfills or to save Eco hours)
heroku ps:scale worker=0 -a gethookery-agency
```

Heroku's combined Eco quota is 1000 dyno-hours/month for all dynos in the
account. With both `web` and `worker` always-on, total usage is ~1440 h/mo
which exceeds the quota — when the math gets tight, either let one dyno
sleep, upgrade `web` to Basic ($7/mo) and keep `worker` on Eco, or run
ingestion as one-off jobs (`heroku run python manage.py import_edgar_form_d`).

Worker tuning via Heroku config vars:

| Var | Default | Effect |
| --- | --- | --- |
| `WORKER_RUN_ON_START` | unset | Run all jobs once at startup. Useful right after a backfill bump. |
| `WORKER_EDGAR_DAYS` | 1 | Days back for the daily EDGAR job. |
| `WORKER_EDGAR_MAX` | 200 | Hard cap on filings per EDGAR run. |
| `WORKER_DISABLE_EDGAR` | unset | Skip scheduling the EDGAR job. |
| `WORKER_DISABLE_GITHUB` | unset | Skip scheduling the GitHub awesome job. |

### OpenVC quarterly export playbook

OpenVC has no API. We refresh from a manual export every quarter:

1. Log in to <https://www.openvc.app/> and apply filters
   (e.g. AI / SaaS, Pre-seed/Seed, ticket within $0.5–5M).
2. Click **Export to CSV** — this downloads a CSV with the column
   headers documented above.
3. In the admin, open *Investors -> Funds -> Import* (top-right) and
   upload the CSV. The OpenVC headers are auto-mapped by `FundResource`.
4. After the import finishes, batch-tag the new rows by thesis using the
   admin filter + `thesis_tags` action.

### Out of scope for Phase 2 (yet)

- Email sending + open/click tracking + reply ingestion
- Cadence/sequence engine and follow-up scheduler
- X/Twitter monitor (planned: Apify free $5/mo + push webhook)
- Hunter / Snov enrichment commands
- Investor-facing landing at `/for-investors` (deck + metrics)

## Contact

`hi@gethookery.com`
