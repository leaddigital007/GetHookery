"""
SEC EDGAR Form D crawler.

Form D is filed by every US private placement and contains:

  * issuer (the company raising money) — name, CIK, address, entity type,
    industry group classification
  * total offering amount and amount actually sold
  * related persons (typically officers / GPs, NOT outside investors)

The actual investors are NOT disclosed on Form D. We still get high-value
signal: a real-time stream of US companies that just closed funding.
For "Pooled Investment Fund" filings, the issuer is itself a VC fund
raising LP capital — those go to the Signal triage queue as candidate
funds to add to our CRM.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from xml.etree import ElementTree as ET

import requests

EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
EDGAR_ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"

# SEC requires a contact in the User-Agent so they can throttle abusers.
USER_AGENT = "Kubricon CRM admin@kubricon.com"

POOLED_FUND_INDUSTRY = "Pooled Investment Fund"


@dataclass
class FilingHit:
    accession: str
    cik: str
    file_date: str
    form: str
    display_name: str


@dataclass
class FormDFiling:
    accession: str
    cik: str
    file_date: str
    form: str
    issuer_name: str
    jurisdiction: str
    entity_type: str
    industry_group: str
    industry_subgroup: str
    total_offering_usd: int | None
    total_sold_usd: int | None
    state_or_country: str
    raw_payload: dict[str, Any]


def _request(url: str, *, params: dict | None = None, accept: str = "application/json") -> requests.Response:
    response = requests.get(
        url,
        params=params,
        headers={"User-Agent": USER_AGENT, "Accept": accept},
        timeout=30,
    )
    response.raise_for_status()
    return response


def search_recent_form_d(
    *,
    start: date,
    end: date,
    query: str | None = None,
    max_results: int = 200,
    sleep_between_pages: float = 0.2,
) -> list[FilingHit]:
    """Page through the EDGAR full-text search for Form D + Form D/A filings."""
    params = {
        "forms": "D,D/A",
        "dateRange": "custom",
        "startdt": start.isoformat(),
        "enddt": end.isoformat(),
    }
    if query:
        params["q"] = query

    out: list[FilingHit] = []
    page_from = 0
    while len(out) < max_results:
        params["from"] = page_from
        response = _request(EDGAR_SEARCH_URL, params=params)
        data = response.json()
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            break
        for hit in hits:
            src = hit.get("_source", {})
            display = (src.get("display_names") or ["?"])[0]
            ciks = src.get("ciks") or []
            if not ciks:
                continue
            out.append(
                FilingHit(
                    accession=src.get("adsh", ""),
                    cik=ciks[0],
                    file_date=src.get("file_date", ""),
                    form=src.get("form", ""),
                    display_name=display,
                )
            )
            if len(out) >= max_results:
                break
        page_from += len(hits)
        if len(hits) < 10:
            break
        time.sleep(sleep_between_pages)
    return out


def _xml_text(root: ET.Element, path: str) -> str:
    el = root.find(path)
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _parse_amount(text: str) -> int | None:
    if not text:
        return None
    cleaned = text.replace(",", "").replace("$", "").strip()
    if not cleaned or cleaned.lower() in ("indefinite", "n/a"):
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def fetch_filing_detail(hit: FilingHit, *, sleep_after: float = 0.15) -> FormDFiling | None:
    """Pull and parse the structured `primary_doc.xml` for a Form D filing."""
    cik_int = int(hit.cik)
    accession_no_dashes = hit.accession.replace("-", "")
    url = f"{EDGAR_ARCHIVE_BASE}/{cik_int}/{accession_no_dashes}/primary_doc.xml"
    response = _request(url, accept="application/xml")
    time.sleep(sleep_after)

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError:
        return None

    issuer_name = _xml_text(root, ".//primaryIssuer/entityName")
    jurisdiction = _xml_text(root, ".//primaryIssuer/jurisdictionOfInc")
    entity_type = _xml_text(root, ".//primaryIssuer/entityType")
    state_or_country = _xml_text(root, ".//primaryIssuer/issuerAddress/stateOrCountryDescription")

    industry_group = _xml_text(root, ".//offeringData/industryGroup/industryGroupType")
    industry_subgroup = (
        _xml_text(root, ".//offeringData/industryGroup/investmentFundType")
        or _xml_text(root, ".//offeringData/industryGroup/issuerSpecificCommercialIndustryType")
    )

    total_offering = _parse_amount(_xml_text(root, ".//offeringSalesAmounts/totalOfferingAmount"))
    total_sold = _parse_amount(_xml_text(root, ".//offeringSalesAmounts/totalAmountSold"))

    return FormDFiling(
        accession=hit.accession,
        cik=hit.cik,
        file_date=hit.file_date,
        form=hit.form,
        issuer_name=issuer_name or hit.display_name,
        jurisdiction=jurisdiction,
        entity_type=entity_type,
        industry_group=industry_group,
        industry_subgroup=industry_subgroup,
        total_offering_usd=total_offering,
        total_sold_usd=total_sold,
        state_or_country=state_or_country,
        raw_payload={
            "issuer_name": issuer_name,
            "jurisdiction": jurisdiction,
            "entity_type": entity_type,
            "industry_group": industry_group,
            "industry_subgroup": industry_subgroup,
            "total_offering_usd": total_offering,
            "total_sold_usd": total_sold,
            "state_or_country": state_or_country,
        },
    )


def derive_stage(amount_usd: int | None) -> str:
    """Approximate stage label for a Form D using common thresholds."""
    if amount_usd is None:
        return ""
    if amount_usd <= 1_500_000:
        return "Pre-seed"
    if amount_usd <= 5_000_000:
        return "Seed"
    if amount_usd <= 20_000_000:
        return "Series A"
    if amount_usd <= 60_000_000:
        return "Series B"
    if amount_usd <= 150_000_000:
        return "Series C"
    return "Series D+"


def default_date_window(days_back: int) -> tuple[date, date]:
    today = date.today()
    return today - timedelta(days=days_back), today
