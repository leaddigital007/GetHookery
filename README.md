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
    llm/                    Phase 3: LLM-driven scoring/tagging/enrichment
                            (Vertex AI + Gemini), provider abstraction,
                            LLMCall audit + budget circuit breaker
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

# Seed baseline thesis + category tags (idempotent)
python manage.py seed_tags

# Auto-assign thesis tags to Funds by keyword match in their portfolio_notes
python manage.py tag_funds_from_portfolio

# Extract portfolio companies from Fund.portfolio_notes,
# create Company rows, and link them via PortfolioMention
python manage.py extract_portfolio_companies
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

### Admin navigation cheatsheet

Common questions and where to answer them:

| You want to... | Open this | Tip |
| --- | --- | --- |
| See **all funds** sorted by tier | `/admin/investors/fund/` | Default ordering is by `tier` then name. |
| See **funds in our thesis** | `/admin/investors/fund/?thesis_tags__id__exact=<tag_id>` | Use the right-hand "Thesis" filter — pick one tag at a time. |
| See **Tier S funds only** | `/admin/investors/fund/?tier__exact=S` | After hand-grading, this is your shortlist for outreach. |
| Bulk-promote selected funds to **Tier S/1/2** | `/admin/investors/fund/`, select rows, action menu | Actions: "Set tier: Tier S / 1 / 2 / Watch". |
| See **what a fund typically backs** | Open any Fund -> "Portfolio mentions" inline at the bottom | Heuristic links extracted from `portfolio_notes`. |
| See **which funds mention a given company** | `/admin/investors/company/?q=<name>` -> open it -> "Mentioned by funds" | Reverse view of the same data. |
| Find **hot bets** (mentioned by many funds) | `/admin/investors/company/?o=4` (sort by "In portfolio of" column) | Companies with 4+ mentions are signal of consensus. |
| See **partners / angels** | `/admin/investors/person/` | Empty until we run the partner-enrichment pipeline (Twitter / Apollo / Hunter). |
| Triage **fresh US deals** | `/admin/ingest/signal/?status__exact=new&kind__exact=new_deal_hint` | Auto-populated daily by SEC EDGAR worker. |
| Triage **new VC funds** raising LPs | `/admin/ingest/signal/?status__exact=new&kind__exact=unmatched_filer` | Pooled-fund Form D filings — promote good ones to a Fund row manually. |
| See **what each ingestion job did** | `/admin/ingest/importrun/` | Shows status, duration, rows created/updated for every run. |
| See **inbound contact form leads** | `/admin/investors/contactsubmission/` | All submissions from the public landing's contact form. |

The Fund list shows pill-style chips for thesis tags and a `Portfolio` count
column so you can spot funds that match your thesis at a glance.

### Where the data comes from

| Surface | Filled by | When |
| --- | --- | --- |
| Funds | `import_github_awesome` (auto, weekly) + OpenVC CSV (manual quarterly) + `+ Fund` (manual) | Out of the box |
| Companies | `extract_portfolio_companies` from fund portfolios + `import_edgar_form_d` (auto, daily) + `+ Company` (manual) | Out of the box + daily |
| Deals | `import_edgar_form_d` (auto, daily) | Daily |
| Persons (partners) | **Empty until Phase 2.3** — needs Apify Twitter scraper or OpenVC paid tier or manual entry | TBD |
| Investments (Fund <-> Deal) | Manual for now (you research and link); auto in a later phase via Crunchbase scrape | TBD |
| Tags | `seed_tags` once + `tag_funds_from_portfolio` auto | Out of the box |
| Portfolio mentions | `extract_portfolio_companies` whenever new funds are imported | After every fund import |

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

## Phase 3 — LLM enrichment (Vertex AI / Gemini 3.1 Pro)

The `apps/llm` app adds an LLM layer for the heavy data-cleanup tasks
where keyword rules don't scale (Tier scoring across 2 500+ funds,
multi-label tagging, comparable-company round extraction, etc.).

Provider abstraction lets us swap Vertex / OpenAI without touching
call sites; current default is `vertex` + `gemini-3.1-pro`. Every call
is audited in `LLMCall` with input hash, token counts and USD cost.
Repeat calls hit the cache by default.

### Required config

Local `.env` and Heroku config vars:

| Var | Default | Purpose |
| --- | --- | --- |
| `LLM_DEFAULT_PROVIDER` | `vertex` | `vertex` or `openai` (only `vertex` wired up today). |
| `LLM_DEFAULT_MODEL` | `gemini-3.1-pro` | Any Vertex model id; `gemini-3.1-flash` is cheaper. |
| `LLM_MAX_DAILY_USD` | `5` | Hard ceiling on rolling 24h LLM spend. |
| `GOOGLE_VERTEX_PROJECT_ID` | — | GCP project hosting Vertex AI. |
| `GOOGLE_VERTEX_LOCATION` | `us-central1` | Vertex region. |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | — | Full service-account JSON pasted as one env var (preferred on Heroku). |
| `GOOGLE_APPLICATION_CREDENTIALS` | — | Filesystem path to a service-account JSON key (local dev). |

The service-account needs the `Vertex AI User` role
(`roles/aiplatform.user`) on the project.

### Available LLM commands

```bash
# Sanity check the wiring (one short prompt, costs ~$0.002)
python manage.py llm_smoke_test

# Score funds: assign Tier S/1/2/watch + relevance score 0-100
python manage.py llm_score_funds --limit 5 --dry-run
python manage.py llm_score_funds --apply --min-score 60
python manage.py llm_score_funds --apply --only-untiered

# Multi-label tagging (broader coverage than keyword triggers)
python manage.py llm_smart_tag_funds --limit 50 --dry-run
python manage.py llm_smart_tag_funds --apply --only-untagged

# Extract comparable companies + their funding rounds
python manage.py llm_extract_comparables --dry-run
python manage.py llm_extract_comparables --apply --names Runway,Pika,Suno
```

All commands run under the same `ingest_run` context as Phase 2
pipelines, so progress shows up in `/admin/ingest/importrun/` next to
EDGAR / GitHub jobs.

## Contact

`hi@gethookery.com`
