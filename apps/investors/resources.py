"""
django-import-export Resource classes for CSV import/export inside the admin.

The Fund resource intentionally also accepts the column names produced by
OpenVC's "Export to CSV" button so we can drop their list straight in.
"""
from __future__ import annotations

from django.utils.text import slugify
from import_export import fields, resources
from import_export.widgets import ForeignKeyWidget, ManyToManyWidget

from .models import Company, Deal, Fund, Investment, Person, Tag


def _parse_money_to_int(value) -> int | None:
    """Parse a free-form money string to an integer USD amount.

    Handles `$`, thousands separators, and `k`/`K`/`m`/`M`/`b`/`B` suffixes,
    including decimal numbers like `$0.5M` -> 500_000.
    Returns None for empty / unparseable input.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) if value == value else None  # filter NaN
    if not isinstance(value, str):
        return None
    s = value.replace("$", "").replace(",", "").replace(" ", "").strip()
    if not s:
        return None
    multiplier = 1
    if s[-1] in ("k", "K"):
        multiplier = 1_000
        s = s[:-1].strip()
    elif s[-1] in ("m", "M"):
        multiplier = 1_000_000
        s = s[:-1].strip()
    elif s[-1] in ("b", "B"):
        multiplier = 1_000_000_000
        s = s[:-1].strip()
    try:
        return int(float(s) * multiplier)
    except (ValueError, TypeError):
        return None


class TagResource(resources.ModelResource):
    class Meta:
        model = Tag
        import_id_fields = ("slug",)
        fields = ("slug", "name", "kind")


class FundResource(resources.ModelResource):
    """Fund import/export.

    On import, the file may use either our internal field names or the
    common OpenVC export headers:
        - "Investor name"  -> name
        - "Website"        -> website
        - "HQ Country"     -> hq_country
        - "HQ City"        -> hq_city
        - "AUM"            -> aum_text
        - "Min Check"      -> check_min_usd
        - "Max Check"      -> check_max_usd
        - "Stages"         -> stages (semicolon or comma separated)
        - "Thesis"         -> thesis_summary
        - "Notable invest" -> portfolio_notes
    """

    thesis_tags = fields.Field(
        attribute="thesis_tags",
        widget=ManyToManyWidget(Tag, field="slug", separator=";"),
    )

    class Meta:
        model = Fund
        import_id_fields = ("slug",)
        fields = (
            "slug",
            "name",
            "website",
            "hq_country",
            "hq_city",
            "aum_text",
            "check_min_usd",
            "check_max_usd",
            "tier",
            "stages",
            "thesis_summary",
            "portfolio_notes",
            "internal_notes",
            "last_activity_at",
            "source",
            "source_url",
            "thesis_tags",
        )
        skip_unchanged = True
        report_skipped = False

    OPENVC_COLUMN_MAP = {
        "investor name": "name",
        "investor": "name",
        "fund name": "name",
        "website": "website",
        "url": "website",
        "hq country": "hq_country",
        "country": "hq_country",
        "hq city": "hq_city",
        "city": "hq_city",
        "aum": "aum_text",
        "min check": "check_min_usd",
        "min check size": "check_min_usd",
        "max check": "check_max_usd",
        "max check size": "check_max_usd",
        "stages": "stages",
        "stage": "stages",
        "thesis": "thesis_summary",
        "investment thesis": "thesis_summary",
        "notable investments": "portfolio_notes",
        "notable invest": "portfolio_notes",
    }

    def before_import(self, dataset, **kwargs):  # noqa: D401
        """Translate OpenVC headers to our internal names before import."""
        new_headers = []
        for header in dataset.headers or []:
            key = (header or "").strip().lower()
            new_headers.append(self.OPENVC_COLUMN_MAP.get(key, header))
        dataset.headers = new_headers
        return super().before_import(dataset, **kwargs)

    def before_import_row(self, row, **kwargs):
        if not row.get("slug") and row.get("name"):
            row["slug"] = slugify(row["name"])[:200]
        stages = row.get("stages")
        if isinstance(stages, str) and stages:
            parts = [p.strip() for p in stages.replace(";", ",").split(",") if p.strip()]
            row["stages"] = parts
        for money_field in ("check_min_usd", "check_max_usd"):
            row[money_field] = _parse_money_to_int(row.get(money_field))
        return super().before_import_row(row, **kwargs)


class PersonResource(resources.ModelResource):
    fund = fields.Field(
        attribute="fund",
        widget=ForeignKeyWidget(Fund, field="slug"),
    )

    class Meta:
        model = Person
        import_id_fields = ("email",)
        fields = (
            "id",
            "fund",
            "full_name",
            "role",
            "email",
            "email_status",
            "twitter_handle",
            "linkedin_url",
            "location",
            "bio_short",
            "pipeline_stage",
            "warmth",
            "internal_notes",
        )


class CompanyResource(resources.ModelResource):
    class Meta:
        model = Company
        import_id_fields = ("slug",)
        fields = (
            "slug",
            "name",
            "website",
            "hq",
            "description",
            "is_kubricon_competitor",
            "relevance_to_kubricon",
        )


class DealResource(resources.ModelResource):
    company = fields.Field(
        attribute="company",
        widget=ForeignKeyWidget(Company, field="slug"),
    )

    class Meta:
        model = Deal
        # `id` keeps existing-row updates idempotent on re-import. CSVs
        # without an `id` column will create new rows; that's expected
        # because Deal has no natural unique key (a company can have
        # multiple rounds at the same stage).
        import_id_fields = ("id",)
        fields = (
            "id",
            "company",
            "amount_usd",
            "stage",
            "announced_at",
            "source_url",
            "notes",
        )


class InvestmentResource(resources.ModelResource):
    fund = fields.Field(
        attribute="fund",
        widget=ForeignKeyWidget(Fund, field="slug"),
    )
    deal = fields.Field(
        attribute="deal",
        widget=ForeignKeyWidget(Deal, field="id"),
    )

    class Meta:
        model = Investment
        # Match the model-level UniqueConstraint on ("fund", "deal") so
        # re-imports update the existing through-row (e.g. flipping
        # is_lead) instead of duplicating it.
        import_id_fields = ("fund", "deal")
        fields = ("id", "fund", "deal", "is_lead", "notes")
