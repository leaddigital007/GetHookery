"""
django-import-export Resource classes for CSV import/export inside the admin.

The Fund resource intentionally also accepts the column names produced by
OpenVC's "Export to CSV" button so we can drop their list straight in.
"""
from __future__ import annotations

import json

from django.utils.text import slugify
from import_export import fields, resources
from import_export.widgets import ForeignKeyWidget, ManyToManyWidget

from .models import Company, Deal, Fund, FundSource, Investment, Person, Tag


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


# US state codes used to detect "City, ST" addresses where the trailing token
# is a state, not a country (e.g. "San Francisco, CA").
_US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}

# Full US state names mapped to canonical country = "USA". OpenVC
# addresses occasionally end with the spelled-out state name like
# "Some address, California 94111".
_US_STATE_NAMES = {
    "ALABAMA", "ALASKA", "ARIZONA", "ARKANSAS", "CALIFORNIA", "COLORADO",
    "CONNECTICUT", "DELAWARE", "FLORIDA", "GEORGIA", "HAWAII", "IDAHO",
    "ILLINOIS", "INDIANA", "IOWA", "KANSAS", "KENTUCKY", "LOUISIANA",
    "MAINE", "MARYLAND", "MASSACHUSETTS", "MICHIGAN", "MINNESOTA",
    "MISSISSIPPI", "MISSOURI", "MONTANA", "NEBRASKA", "NEVADA",
    "OHIO", "OKLAHOMA", "OREGON", "PENNSYLVANIA", "TENNESSEE", "TEXAS",
    "UTAH", "VERMONT", "VIRGINIA", "WASHINGTON", "WISCONSIN", "WYOMING",
}

# Country names we want to canonicalise so the admin filter shows a
# single bucket (e.g. "USA", not "USA" and "United States" both).
_COUNTRY_NAME_NORMALISATION = {
    "UNITED STATES": "USA",
    "UNITED STATES OF AMERICA": "USA",
    "U.S.": "USA",
    "U.S.A.": "USA",
    "AMERICA": "USA",
    "UNITED KINGDOM": "United Kingdom",
    "GREAT BRITAIN": "United Kingdom",
    "ENGLAND": "United Kingdom",
    "UNITED ARAB EMIRATES": "UAE",
}

# Region / province codes for non-US countries that may appear before the
# trailing country token (e.g. "Toronto, ON, Canada", "Sydney, NSW, Australia").
_NON_US_REGION_CODES = {
    # Canada
    "AB", "BC", "MB", "NB", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT",
    # Australia
    "NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT",
}

# Map common 2-3 letter country codes used by OpenVC to canonical names
# we want to filter on in admin (`hq_country`).
_COUNTRY_CODE_MAP = {
    "US": "USA", "USA": "USA",
    "UK": "United Kingdom", "GB": "United Kingdom",
    "NL": "Netherlands",
    "DE": "Germany",
    "FR": "France",
    "ES": "Spain",
    "IT": "Italy",
    "CH": "Switzerland",
    "AT": "Austria",
    "BE": "Belgium",
    "SE": "Sweden",
    "NO": "Norway",
    "DK": "Denmark",
    "FI": "Finland",
    "IE": "Ireland",
    "PL": "Poland",
    "PT": "Portugal",
    "EE": "Estonia",
    "LV": "Latvia",
    "LT": "Lithuania",
    "UA": "Ukraine",
    "RU": "Russia",
    "TR": "Turkey",
    "IL": "Israel",
    "AE": "UAE", "UAE": "UAE",
    "SA": "Saudi Arabia",
    "EG": "Egypt",
    "ZA": "South Africa",
    "NG": "Nigeria",
    "KE": "Kenya",
    "IN": "India",
    "PK": "Pakistan",
    "CN": "China",
    "HK": "Hong Kong",
    "TW": "Taiwan",
    "JP": "Japan",
    "KR": "South Korea",
    "SG": "Singapore",
    "TH": "Thailand",
    "VN": "Vietnam",
    "ID": "Indonesia",
    "MY": "Malaysia",
    "PH": "Philippines",
    "AU": "Australia",
    "NZ": "New Zealand",
    "CA": "Canada",
    "MX": "Mexico",
    "BR": "Brazil",
    "AR": "Argentina",
    "CL": "Chile",
    "CO": "Colombia",
    "PE": "Peru",
    "UY": "Uruguay",
}


def _alpha_words(token: str) -> list[str]:
    """Split `token` into words, keeping only those with at least one
    alphabetic character. Effectively strips zip codes and lone dashes."""
    return [w for w in (token or "").split() if any(ch.isalpha() for ch in w)]


def _is_region_token(token: str) -> bool:
    """True if a comma-separated chunk is *purely* a region marker -
    a US state code/name, a Canadian/Australian province code, or
    all-numeric zip - i.e. it carries no city information of its own.
    """
    if not token:
        return True
    words = _alpha_words(token)
    if not words:
        return True  # all digits / punctuation
    if len(words) == 1:
        single = words[0].upper()
        if single in _US_STATE_CODES or single in _NON_US_REGION_CODES:
            return True
    full = " ".join(w.upper() for w in words)
    if full in _US_STATE_NAMES:
        return True
    return False


def _extract_city_text(token: str) -> str:
    """Strip pure-digit / pure-punct words and return the trimmed
    remainder. "80333 Munich" -> "Munich"; "Mumbai – 400051" -> "Mumbai".
    """
    return " ".join(_alpha_words(token)).strip(" -–—.")


def _looks_like_clean_city(token: str) -> bool:
    """True if token looks like a plain alpha-only city name (no
    digits) and is not itself a region/state marker."""
    if not token or any(ch.isdigit() for ch in token):
        return False
    return not _is_region_token(token)


def _parse_hq(hq_str) -> tuple[str, str]:
    """Heuristically split a free-form HQ address into (city, country).

    Examples:
        "San Francisco, CA"                                -> ("San Francisco", "USA")
        "541 Jefferson Ave., Redwood City, CA 94063"       -> ("Redwood City", "USA")
        "Tiburon, California, 94920"                       -> ("Tiburon", "USA")
        "Boston, MA, USA"                                  -> ("Boston", "USA")
        "Singel 542, Amsterdam, North Holland 1017 AZ, NL" -> ("Amsterdam", "Netherlands")
        "Toronto, ON, Canada"                              -> ("Toronto", "Canada")
        "Sydney, NSW, Australia"                           -> ("Sydney", "Australia")
        "Briennerstraße 21, 80333 Munich, Germany"         -> ("Munich", "Germany")

    Best-effort. Goal: produce *something* to filter on in admin,
    not perfect geocoding.
    """
    if not hq_str:
        return ("", "")
    parts = [p.strip() for p in str(hq_str).split(",") if p.strip()]
    if not parts:
        return ("", "")

    # Strip a trailing all-digits zip-code token like "94920" so the next
    # token (state or country) can be detected properly.
    while parts and parts[-1].replace(" ", "").replace("-", "").isdigit():
        parts.pop()
    if not parts:
        return ("", "")

    last = parts[-1]
    last_clean_tokens = [
        t for t in last.upper().split()
        if t and not t.replace("-", "").isdigit()
    ]
    if last_clean_tokens:
        last_first = last_clean_tokens[0]
        last_full = " ".join(last_clean_tokens)
    else:
        last_first = last.upper()
        last_full = last.upper()

    if last_first in _US_STATE_CODES or last_full in _US_STATE_NAMES:
        canonical = "USA"
    else:
        canonical = (
            _COUNTRY_NAME_NORMALISATION.get(last_full)
            or _COUNTRY_CODE_MAP.get(last_first, last)
        )

    # Walk backwards over tokens that are *purely* region markers (US
    # state code/name, Canadian/Australian province code, all-numeric
    # zip) so we don't pick "ON" as the city for "Toronto, ON, Canada".
    city_idx = -2
    while abs(city_idx) <= len(parts) and _is_region_token(parts[city_idx]):
        city_idx -= 1

    # If we landed on a "Region/State + Zip" combo that contains
    # multi-word region text (e.g. "North Holland 1017 AZ") AND the
    # previous chunk is a clean alpha-only city name (e.g. "Amsterdam"),
    # prefer that earlier chunk over partial extraction. Single-word
    # mixed tokens like "80333 Munich" or "Mumbai - 400051" stay as-is
    # so we can extract the city via _extract_city_text.
    if (
        abs(city_idx) <= len(parts)
        and any(ch.isdigit() for ch in parts[city_idx])
        and abs(city_idx - 1) <= len(parts)
        and len(_alpha_words(parts[city_idx])) >= 2
        and _looks_like_clean_city(parts[city_idx - 1])
    ):
        city_idx -= 1

    city_token = parts[city_idx] if abs(city_idx) <= len(parts) else ""
    city = _extract_city_text(city_token)[:120]
    return (city, canonical[:80])


def _normalize_stage_label(label: str) -> str:
    """Strip OpenVC ordering prefixes like "1. Idea or Patent" -> "Idea or Patent"."""
    s = (label or "").strip()
    if not s:
        return ""
    if len(s) >= 3 and s[0].isdigit() and s[1] == "." and s[2] == " ":
        s = s[3:].strip()
    elif len(s) >= 4 and s[0:2].isdigit() and s[2] == "." and s[3] == " ":
        s = s[4:].strip()
    return s


def _stages_to_json(value) -> str:
    """Coerce any "stages" payload from a row into a JSON-encoded list.

    Accepts:
      - a list of strings (possibly with whitespace / empty / non-str junk)
      - a free-form string with comma- or semicolon-separated stage labels
      - empty string, whitespace, None, or anything else -> "[]"

    Empty / whitespace-only items are dropped *both* before and after
    label normalisation so that the same input always produces the same
    output regardless of whether it arrives as a list or as a delimited
    string.
    """
    if isinstance(value, list):
        items = [
            p for p in value
            if isinstance(p, str) and p.strip()
        ]
    elif isinstance(value, str) and value.strip():
        items = [
            p for p in value.replace(";", ",").split(",")
            if p.strip()
        ]
    else:
        return "[]"

    cleaned = [
        normalised
        for normalised in (_normalize_stage_label(p) for p in items)
        if normalised
    ]
    return json.dumps(cleaned)


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

    # Maps lowercased CSV/XLSX header text to our internal column name.
    # Special targets prefixed with "_" are intermediate fields consumed by
    # `before_import_row` (parsed into `internal_notes`, `hq_country`, etc.).
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
        "global hq": "_hq_raw",
        "aum": "aum_text",
        "min check": "check_min_usd",
        "min check size": "check_min_usd",
        "first cheque minimum": "check_min_usd",
        "max check": "check_max_usd",
        "max check size": "check_max_usd",
        "first cheque maximum": "check_max_usd",
        "stages": "stages",
        "stage": "stages",
        "stage of investment": "stages",
        "thesis": "thesis_summary",
        "investment thesis": "thesis_summary",
        "notable investments": "portfolio_notes",
        "notable invest": "portfolio_notes",
        "investor type": "_investor_type",
        "countries of investment": "_countries_of_investment",
    }

    def before_import(self, dataset, **kwargs):  # noqa: D401
        """Translate OpenVC headers to our internal names before import.

        We also pre-populate empty columns for every model field we may
        write inside `before_import_row` (slug, internal_notes, source...).
        Without this, django-import-export 4.x's failed-row collector
        raises ``InvalidDimensions`` when our hook adds keys that the
        original dataset header doesn't know about.
        """
        new_headers = []
        for header in dataset.headers or []:
            key = (header or "").strip().lower()
            new_headers.append(self.OPENVC_COLUMN_MAP.get(key, header))
        dataset.headers = new_headers

        expected_columns = (
            "slug", "name", "website", "hq_country", "hq_city", "aum_text",
            "check_min_usd", "check_max_usd", "tier", "stages",
            "thesis_summary", "portfolio_notes", "internal_notes",
            "last_activity_at", "source", "source_url", "thesis_tags",
        )
        for col in expected_columns:
            if col not in dataset.headers:
                dataset.append_col([""] * dataset.height, header=col)

        return super().before_import(dataset, **kwargs)

    def before_import_row(self, row, **kwargs):
        # IMPORTANT: do not delete synthetic keys (`_hq_raw`, `_investor_type`,
        # `_countries_of_investment`) - removing keys here breaks
        # django-import-export's failed-row collector which expects the
        # row dict's width to stay equal to ``dataset.headers``.
        # Unknown keys are silently ignored by Resource.fields anyway.

        if not row.get("slug") and row.get("name"):
            row["slug"] = slugify(row["name"])[:200]

        hq_raw = row.get("_hq_raw")
        if hq_raw and not row.get("hq_country") and not row.get("hq_city"):
            city, country = _parse_hq(hq_raw)
            row["hq_city"] = city
            row["hq_country"] = country

        # Normalise `stages` into a JSON-encoded list. The Fund model uses a
        # JSONField with default=list, and django-import-export's JSONWidget
        # calls json.loads() on whatever string we leave here, so we always
        # emit valid JSON. List- and string-shaped inputs share the same
        # filter logic via `_stages_to_json` to keep them in sync.
        row["stages"] = _stages_to_json(row.get("stages"))

        for money_field in ("check_min_usd", "check_max_usd"):
            row[money_field] = _parse_money_to_int(row.get(money_field))

        # Capture investor type + invest-in countries inside internal_notes
        # since we don't have dedicated columns for them yet.
        notes_extras: list[str] = []
        investor_type = (row.get("_investor_type") or "").strip() if isinstance(row.get("_investor_type"), str) else ""
        if investor_type:
            notes_extras.append(f"OpenVC type: {investor_type}")
        countries = row.get("_countries_of_investment") or ""
        if countries:
            country_list = [c.strip() for c in str(countries).split(",") if c.strip()][:8]
            if country_list:
                notes_extras.append("Invests in: " + ", ".join(country_list))
        if notes_extras:
            existing = (row.get("internal_notes") or "").strip()
            row["internal_notes"] = (existing + ("\n" if existing else "") + "\n".join(notes_extras))[:4000]

        # Default the source to OpenVC when this resource is fed an OpenVC
        # export and no explicit source column was provided.
        if not row.get("source"):
            row["source"] = FundSource.OPENVC

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
