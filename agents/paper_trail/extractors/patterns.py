"""Deterministic regex patterns for freight invoice field extraction.

Each public function takes a text blob (full document, all pages joined with
--- PAGE N --- separators) and returns the extracted value or None.

Design discipline
-----------------
These extractors run BEFORE the LLM call.  Whatever they find is passed to
the LLM as "pre-extracted — confirm or correct" context.  Whatever they miss
is left for the LLM to extract from scratch.  This means:

  - False positives here are caught by the LLM cross-check (cheaper than
    fixing a hallucination downstream).
  - False negatives here simply add to the LLM's extraction scope (not a
    failure path).

Pattern priority
----------------
Within each function, patterns are tried in order: most-specific (label-
anchored) first, then structural fallback.  First match wins.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Literal

# ---------------------------------------------------------------------------
# Compiled patterns — module level for performance
# ---------------------------------------------------------------------------

# Dollar amounts: $3,920.00  $640.00  $ 70.00
_AMOUNT_LABEL_TOTAL = re.compile(
    r"""(?:Total\s+(?:Amount\s+)?Due|Amount\s+Due|Total\s+Charges?|Grand\s+Total)
        \s*[:\-]?\s*
        \$\s*([\d,]+\.\d{2})""",
    re.IGNORECASE | re.VERBOSE,
)
_AMOUNT_LABEL_RATE = re.compile(
    r"""(?:Rate|Hourly\s+Rate|Per[-\s](?:Hour|Day)\s+Rate)
        \s*[:\-]?\s*
        \$\s*([\d,]+\.\d{2})\s*/\s*(?:hr|hour|day)""",
    re.IGNORECASE | re.VERBOSE,
)
_AMOUNT_BARE = re.compile(r"\$\s*([\d,]+\.\d{2})")

# Dates: ISO first, then US long, then US short
_DATE_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DATE_US_LONG = re.compile(
    r"\b((?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},\s+\d{4})\b",
    re.IGNORECASE,
)
_DATE_US_SHORT = re.compile(r"\b(\d{1,2}/\d{1,2}/(\d{4}))\b")

# ISO container: 4 alpha owner code + 6 digits + 1 check digit = 11 chars total
# MEDU9871234, TCLU4470129, CMAU7723445
_CONTAINER = re.compile(r"\b([A-Z]{4}\d{7})\b")

# BOL number — label-anchored first, structural fallback second
_BOL_LABELED = re.compile(
    r"(?:Bill\s+of\s+Lading|BOL)\s*(?:No\.?|Number|No)?\s*[:\-]?\s*([A-Z]{3,6}\d{4,}[\w\-]*)",
    re.IGNORECASE,
)
_BOL_FALLBACK = re.compile(r"\b([A-Z]{3,6}\d{8,}[\w\-]*)\b")

# Invoice number — label-anchored only (too many false positives without label)
_INVOICE_NUMBER = re.compile(
    r"Invoice\s+(?:No\.?|Number|No)\s*[:\-]?\s*([A-Z\d][\w\-]+)",
    re.IGNORECASE,
)

# Hours billed: "56.0 hours"  "32 hrs"
_HOURS_BILLED = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\s+(?:billed|charged)", re.IGNORECASE)
_HOURS_GENERAL = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\b", re.IGNORECASE)

# Days billed (demurrage typically): "3 calendar days"  "2.33 days"
_DAYS_BILLED = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(?:calendar\s+)?days?\s+(?:billed|charged|demurrage)",
    re.IGNORECASE,
)

# Hourly / daily rate with unit suffix
_RATE_PER_HOUR = re.compile(
    r"\$\s*([\d,]+\.\d{2})\s*/\s*(?:hr|hour)", re.IGNORECASE
)
_RATE_PER_DAY = re.compile(
    r"\$\s*([\d,]+\.\d{2})\s*/\s*day", re.IGNORECASE
)

# Tariff reference — two patterns in priority order:
# 1. Label-anchored: "Tariff Reference: tariff EMFR-2024-DEM"
# 2. Inline: "per tariff EMFR-2024-DEM" — requires number to contain a digit (avoids
#    matching "Tariff Reference" where the word after "tariff" is "Reference" with no digits)
_TARIFF_REF_LABELED = re.compile(
    r"Tariff\s+Reference\s*[:\-]\s*tariff\s+([A-Z]{2,}[\w\-]+)",
    re.IGNORECASE,
)
_TARIFF_REF_INLINE = re.compile(
    r"(?:per\s+)?tariff\s+([A-Z]{2,}[\w]*\d[\w\-]*)",
    re.IGNORECASE,
)

# Free time — hours: "72 hours free time"
_FREE_TIME_HOURS = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\s+free\s+time", re.IGNORECASE
)
# Free time — days: "3 days free time" OR "Published Free Time: 3 days"
_FREE_TIME_DAYS = re.compile(
    r"""(?:
        \b(\d+(?:\.\d+)?)\s*(?:calendar\s+)?days?\s+free\s+time   # "3 days free time"
        |
        Published\s+Free\s+Time\s*[:\-]\s*(\d+(?:\.\d+)?)\s*days? # "Published Free Time: 3 days"
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Free time start / end — labeled
_FREE_TIME_START_LABELED = re.compile(
    r"""(?:Free\s+Time\s+(?:Start|Begin)|Availability\s+Date)\s*[:\-]?\s*
        (\d{4}-\d{2}-\d{2}|\b(?:January|February|March|April|May|June|July|
        August|September|October|November|December)\s+\d{1,2},\s+\d{4})""",
    re.IGNORECASE | re.VERBOSE,
)
_FREE_TIME_END_LABELED = re.compile(
    r"""(?:Free\s+Time\s+(?:End|Expir\w*)|Last\s+Free\s+Day)\s*[:\-]?\s*
        (\d{4}-\d{2}-\d{2}|\b(?:January|February|March|April|May|June|July|
        August|September|October|November|December)\s+\d{1,2},\s+\d{4})""",
    re.IGNORECASE | re.VERBOSE,
)

# Charge incurred date — labeled
_CHARGE_INCURRED_LABELED = re.compile(
    r"""(?:Charge\s+(?:Incurred|Start)\s+Date|Demurrage\s+(?:Start|Begin)\s+Date)
        \s*[:\-]?\s*
        (\d{4}-\d{2}-\d{2}|\b(?:January|February|March|April|May|June|July|
        August|September|October|November|December)\s+\d{1,2},\s+\d{4})""",
    re.IGNORECASE | re.VERBOSE,
)

# Invoice date — labeled
_INVOICE_DATE_LABELED = re.compile(
    r"""(?:Invoice\s+Date|Date\s+of\s+Invoice|Dated)\s*[:\-]?\s*
        (\d{4}-\d{2}-\d{2}|\b(?:January|February|March|April|May|June|July|
        August|September|October|November|December)\s+\d{1,2},\s+\d{4})""",
    re.IGNORECASE | re.VERBOSE,
)


# ---------------------------------------------------------------------------
# Helper: parse a date string in ISO or US long format
# ---------------------------------------------------------------------------

def _parse_date(s: str) -> date | None:
    """Parse ISO (2026-06-25) or US long (June 25, 2026) date strings."""
    s = s.strip()
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _strip_amount(s: str) -> float | None:
    """Convert '$3,920.00' or '3,920.00' to float."""
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Public extractors
# ---------------------------------------------------------------------------

def extract_invoice_number(text: str) -> str | None:
    """Extract invoice number from label-anchored 'Invoice No.: EMFR-INV-2026-0491'.

    Example match: 'Invoice No.: EMFR-INV-2026-0491' → 'EMFR-INV-2026-0491'
    """
    m = _INVOICE_NUMBER.search(text)
    return m.group(1).strip() if m else None


def extract_bol_number(text: str) -> str | None:
    """Extract BOL number, label-anchored first, structural fallback second.

    Example match: 'BOL No.: EMFR20260401-001' → 'EMFR20260401-001'
    Fallback: 'EMFR20260401001' → 'EMFR20260401001' (8+ digit structural)
    """
    m = _BOL_LABELED.search(text)
    if m:
        return m.group(1).strip()
    m = _BOL_FALLBACK.search(text)
    return m.group(1).strip() if m else None


def extract_container_number(text: str) -> str | None:
    """Extract ISO 6346 container number: 4 alpha + 7 digits.

    Example match: 'MEDU9871234' → 'MEDU9871234'
    """
    m = _CONTAINER.search(text)
    return m.group(1) if m else None


def extract_total_amount(text: str) -> float | None:
    """Extract total invoice amount, label-anchored.

    Example match: 'Total Amount Due: $3,920.00' → 3920.0
    """
    m = _AMOUNT_LABEL_TOTAL.search(text)
    if m:
        return _strip_amount(m.group(1))
    # fallback: largest bare dollar amount in document
    amounts = [_strip_amount(m.group(1)) for m in _AMOUNT_BARE.finditer(text)]
    amounts_valid = [a for a in amounts if a is not None and a > 0]
    return max(amounts_valid) if amounts_valid else None


def extract_hourly_rate(text: str) -> tuple[float | None, Literal["hourly", "daily"]]:
    """Extract rate and billing unit from '$70.00/hr' or '$25.00/day'.

    Returns (rate, unit). Unit is 'daily' when the invoice uses /day notation.
    Example: '$70.00/hr' → (70.0, 'hourly')
    Example: '$25.00/day' → (25.0, 'daily')
    """
    m = _RATE_PER_HOUR.search(text)
    if m:
        return _strip_amount(m.group(1)), "hourly"
    m = _RATE_PER_DAY.search(text)
    if m:
        return _strip_amount(m.group(1)), "daily"
    return None, "hourly"


def extract_hours_billed(text: str, billing_unit: Literal["hourly", "daily"] = "hourly") -> float | None:
    """Extract hours billed, normalising days × 24 when billing_unit is 'daily'.

    Example (hourly): '56.0 hours billed' → 56.0
    Example (daily):  '3 days billed'     → 72.0  (3 × 24)
    """
    m = _HOURS_BILLED.search(text) or _HOURS_GENERAL.search(text)
    if m:
        return float(m.group(1))
    if billing_unit == "daily":
        m = _DAYS_BILLED.search(text)
        if m:
            return float(m.group(1)) * 24
    return None


def extract_invoice_date(text: str) -> date | None:
    """Extract invoice date from labeled 'Invoice Date: 2026-06-25'.

    Example match: 'Invoice Date: June 25, 2026' → date(2026, 6, 25)
    """
    m = _INVOICE_DATE_LABELED.search(text)
    return _parse_date(m.group(1)) if m else None


def extract_charge_incurred_date(text: str) -> date | None:
    """Extract charge incurred date from labeled field.

    Example match: 'Charge Incurred Date: 2026-05-09' → date(2026, 5, 9)
    """
    m = _CHARGE_INCURRED_LABELED.search(text)
    return _parse_date(m.group(1)) if m else None


def extract_free_time_start(text: str) -> date | None:
    """Extract free time start date from labeled field.

    Example match: 'Free Time Start: 2026-05-07' → date(2026, 5, 7)
    Note: PaperTrail extracts the date component only; UTC time is defaulted
    to 00:00:00 UTC by the LLM layer when constructing the datetime field.
    """
    m = _FREE_TIME_START_LABELED.search(text)
    return _parse_date(m.group(1)) if m else None


def extract_free_time_end(text: str) -> date | None:
    """Extract free time end date from labeled field.

    Example match: 'Free Time End: 2026-05-09' → date(2026, 5, 9)
    """
    m = _FREE_TIME_END_LABELED.search(text)
    return _parse_date(m.group(1)) if m else None


def extract_tariff_number(text: str) -> str | None:
    """Extract tariff reference number, label-anchored first.

    Example match: 'Tariff Reference: tariff EMFR-2024-DEM' → 'EMFR-2024-DEM'
    Example match: 'per tariff EMFR-2024-DEM' → 'EMFR-2024-DEM'
    """
    m = _TARIFF_REF_LABELED.search(text)
    if m:
        return m.group(1).strip()
    m = _TARIFF_REF_INLINE.search(text)
    return m.group(1).strip() if m else None


def extract_free_time_days(text: str) -> float | None:
    """Extract published free time in days.

    Example match: '3 days free time' → 3.0
    Example match: 'Published Free Time: 3 days' → 3.0
    Falls back to hours / 24 when hours pattern matches instead.
    """
    m = _FREE_TIME_DAYS.search(text)
    if m:
        # Two capture groups: group(1) for "N days free time", group(2) for label-anchored
        val = m.group(1) or m.group(2)
        return float(val)
    m = _FREE_TIME_HOURS.search(text)
    if m:
        return float(m.group(1)) / 24
    return None
