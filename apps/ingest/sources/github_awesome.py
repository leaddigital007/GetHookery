"""
Parser for "awesome-vc"-style markdown lists hosted on GitHub.

These lists follow a common pattern: a single pipe table with the columns

    | VC | Stage | Ticket size | HQ | Exemplary investments |

with the VC name wrapped in a markdown link to the fund's website. The
parser is intentionally tolerant: column order and exact header names can
vary, and we ignore extra columns we don't recognize.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import requests

# Default seed lists. Extra ones can be added via the management command's
# --url argument without code changes.
DEFAULT_LISTS: list["AwesomeListSpec"] = []


@dataclass(frozen=True)
class AwesomeListSpec:
    url: str
    source: str  # value stored in ExternalRef.source / ImportRun.source

    @property
    def external_id_prefix(self) -> str:
        return self.source


DEFAULT_LISTS = [
    AwesomeListSpec(
        url="https://raw.githubusercontent.com/jonathimer/awesome-oss-investors/main/README.md",
        source="github_awesome_oss",
    ),
]


_HEADER_ROW_RE = re.compile(r"^\|.+\|\s*$")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BARE_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_SEP_ALPHABET = set("|-: \t")


def _is_separator(line: str) -> bool:
    """A markdown table separator: only |, -, :, whitespace, with at least one dash."""
    stripped = line.strip()
    if not stripped or "-" not in stripped:
        return False
    return all(ch in _SEP_ALPHABET for ch in stripped)


def _looks_like_table_row(line: str) -> bool:
    """A data row contains at least one pipe and is not a separator line."""
    if not line.strip():
        return False
    if _is_separator(line):
        return False
    return line.count("|") >= 1


def fetch_markdown(url: str, *, timeout: int = 30) -> str:
    """Fetch a raw markdown file from GitHub (or anywhere)."""
    response = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": "Kubricon-CRM/1.0 (+https://gethookery-agency-3cc368fea69d.herokuapp.com)",
            "Accept": "text/plain, text/markdown",
        },
    )
    response.raise_for_status()
    return response.text


def split_row(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [cell.strip() for cell in inner.split("|")]


def parse_pipe_tables(md_text: str) -> list[dict]:
    """Return every pipe-style table in the document.

    Each entry is `{"header": [...], "rows": [[...], ...]}`.

    Tolerates GitHub-flavored markdown variations where data rows may omit
    the leading or trailing `|`.
    """
    lines = md_text.splitlines()
    tables: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if (
            _HEADER_ROW_RE.match(line)
            and i + 1 < len(lines)
            and _is_separator(lines[i + 1])
        ):
            header = split_row(line)
            i += 2
            rows: list[list[str]] = []
            while i < len(lines):
                current = lines[i]
                if not _looks_like_table_row(current):
                    break
                cells = split_row(current)
                # Drop rows that look entirely empty.
                if any(c.strip() for c in cells):
                    rows.append(cells)
                i += 1
            tables.append({"header": header, "rows": rows})
        else:
            i += 1
    return tables


def parse_first_link(text: str) -> tuple[str, str | None]:
    """Return (label, href). If `text` has no link, label is the cleaned text."""
    match = _MD_LINK_RE.search(text)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    bare = _BARE_URL_RE.search(text)
    if bare:
        return text.strip(), bare.group(0).strip()
    return text.strip(), None


def _column_index(headers: list[str], aliases: tuple[str, ...]) -> int | None:
    for idx, raw in enumerate(headers):
        norm = raw.strip().lower()
        for alias in aliases:
            if alias in norm:
                return idx
    return None


@dataclass
class FundRow:
    name: str
    website: str
    stage_text: str
    ticket_text: str
    hq_text: str
    portfolio_notes: str
    raw_row: list[str]


def parse_fund_rows(table: dict) -> list[FundRow]:
    headers = table["header"]
    name_idx = _column_index(headers, ("vc", "fund", "investor", "name"))
    if name_idx is None:
        return []
    stage_idx = _column_index(headers, ("stage",))
    ticket_idx = _column_index(headers, ("ticket", "check"))
    hq_idx = _column_index(headers, ("hq", "country", "location"))
    portfolio_idx = _column_index(
        headers, ("invest", "portfolio", "exemplary", "examples")
    )

    out: list[FundRow] = []
    for row in table["rows"]:
        if name_idx >= len(row):
            continue
        raw_name_cell = row[name_idx]
        name, website = parse_first_link(raw_name_cell)
        # Strip footnote markers like (*) so dedupe by name works.
        clean_name = re.sub(r"[*†]+", "", name).strip()
        if not clean_name:
            continue
        out.append(
            FundRow(
                name=clean_name,
                website=website or "",
                stage_text=(row[stage_idx] if stage_idx is not None and stage_idx < len(row) else "").strip(),
                ticket_text=(row[ticket_idx] if ticket_idx is not None and ticket_idx < len(row) else "").strip(),
                hq_text=(row[hq_idx] if hq_idx is not None and hq_idx < len(row) else "").strip(),
                portfolio_notes=(row[portfolio_idx] if portfolio_idx is not None and portfolio_idx < len(row) else "").strip(),
                raw_row=row,
            )
        )
    return out


_TICKET_NUMBER_RE = re.compile(r"\$?\s*([\d.]+)\s*([kKmM]?)")


def parse_ticket_range(ticket_text: str) -> tuple[int | None, int | None]:
    """Pull `(min_usd, max_usd)` out of strings like `$0.5-5M`, `$1m-$50m`, `$250k-4M`."""
    if not ticket_text:
        return None, None
    matches = _TICKET_NUMBER_RE.findall(ticket_text)
    nums: list[int] = []
    for value, suffix in matches:
        try:
            base = float(value)
        except ValueError:
            continue
        unit = suffix.lower()
        if unit == "m":
            nums.append(int(base * 1_000_000))
        elif unit == "k":
            nums.append(int(base * 1_000))
        else:
            # Heuristic: bare numbers > 1000 mean USD, smaller mean millions.
            nums.append(int(base * 1_000_000) if base < 1000 else int(base))
    if not nums:
        return None, None
    if len(nums) == 1:
        return nums[0], nums[0]
    return min(nums), max(nums)


def normalize_stages(stage_text: str) -> list[str]:
    if not stage_text:
        return []
    raw = re.split(r"[,/;]+|\s+and\s+", stage_text)
    out: list[str] = []
    for piece in raw:
        piece = piece.strip().rstrip(".").lower()
        if not piece:
            continue
        # Normalize a few common labels.
        piece = piece.replace("series ", "Series ")
        piece = piece.replace("pre-seed", "Pre-seed")
        piece = piece.replace("preseed", "Pre-seed")
        piece = piece.replace("seed", "Seed") if piece == "seed" else piece
        piece = piece.replace("multi stage", "Multi-stage")
        piece = piece.replace("late stage", "Late-stage")
        # Title-case anything that's still lowercase.
        if piece.islower():
            piece = piece.capitalize()
        out.append(piece)
    return out
