"""PaperTrail — LangChain document extraction agent.

PaperTrail is the Stage 2 document extractor in the DisputeOps case lifecycle.
It converts raw freight invoice documents (PDF, email, EDI 210) into structured
DisputeCase objects consumed by the three Stage 3 agents.

Framework: LangChain 0.3.x
  EXTRACTION_PROMPT | get_llm_client() | with_structured_output(Invoice)
  Same shared get_llm_client() factory as DemandSmith and ComplianceAuditor.

Hybrid extraction discipline
-----------------------------
Deterministic regex runs first (extractors/patterns.py).  Pre-extracted fields
are passed to the LLM as "confirm or correct" context — the LLM cross-checks
rather than re-extracts from scratch.  This catches OCR errors and regex false
positives without a second API call.  Fields the regex misses (carrier_name,
billed_to_party, charge_type, basis_for_charge) are extracted by the LLM only.

EvidencePackage scope
---------------------
PaperTrail populates only evidence fields that appear in the source document:
  - carrier_tariff_reference (from tariff number, if found)
  - published_free_time_days (from free-time disclosure, if found)
  - bol_terms (if BOL terms section appears in the document)
  - shipper_name (from "Bill To" / billed_to_party, if identifiable)

All other EvidencePackage fields (gate timestamps, terminal records,
appointment times) are populated by downstream Stage 2 components
(GateHarvester, CarrierBridge) during Maestro Case execution.

Model string format
-------------------
PAPER_TRAIL_MODEL env var: bare model name (e.g. 'gpt-4o-mini').
No 'openai:' prefix — same convention as ComplianceAuditor and DemandSmith.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import date, datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents.paper_trail.extractors.email import extract_text_from_email
from agents.paper_trail.extractors.patterns import (
    extract_bol_number,
    extract_charge_incurred_date,
    extract_container_number,
    extract_free_time_days,
    extract_free_time_end,
    extract_free_time_start,
    extract_hourly_rate,
    extract_hours_billed,
    extract_invoice_date,
    extract_invoice_number,
    extract_tariff_number,
    extract_total_amount,
)
from agents.paper_trail.extractors.pdf import extract_text_from_pdf
from agents.paper_trail.prompts import EXTRACTION_PROMPT, build_pre_extracted_context
from agents.shared.llm_client import get_llm_client
from data.schemas.case import (
    DisputeCase,
    EvidencePackage,
    Invoice,
    TariffReference,
)

logger = logging.getLogger(__name__)

_MODEL_NAME: str = os.environ.get("PAPER_TRAIL_MODEL", "gpt-4o-mini")


# ---------------------------------------------------------------------------
# ExtractionSource discriminated union
# ---------------------------------------------------------------------------


class PdfSource(BaseModel):
    """Raw PDF bytes for invoice extraction.

    Attributes:
        content: Raw PDF file bytes.
        filename: Optional filename for logging context.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["pdf"] = "pdf"
    content: bytes
    filename: str | None = None


class EmailSource(BaseModel):
    """Full RFC 2822 email message for invoice extraction.

    PaperTrail parses the MIME tree, extracts plain-text body, and
    routes any PDF attachments through the PDF extractor.

    Attributes:
        raw_message: Complete email message string (headers + body).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["email"] = "email"
    raw_message: str


class EdiSource(BaseModel):
    """EDI X12 210 freight invoice message.

    v1 stub — raises NotImplementedError.  Full X12 segment-loop parser
    is deferred to v2.  The ISA/IEA envelope structure and ST/SE 210
    transaction set are documented in the v2 implementation plan.

    Attributes:
        content: Raw ISA..IEA envelope text.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["edi"] = "edi"
    content: str


ExtractionSource = Annotated[
    PdfSource | EmailSource | EdiSource,
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# case_id generation
# ---------------------------------------------------------------------------


def _generate_case_id(carrier_name: str, invoice_number: str, override: str | None) -> str:
    """Generate a deterministic case_id from carrier name + invoice number.

    Production path: DEMO-{8-char hash}-{year}
    Test path: pass override to match fixture IDs exactly.
    """
    if override:
        return override
    combined = f"{carrier_name.upper()}:{invoice_number.upper()}"
    short_hash = hashlib.sha256(combined.encode()).hexdigest()[:8].upper()
    year = datetime.now(tz=timezone.utc).year
    return f"DEMO-{short_hash}-{year}"


# ---------------------------------------------------------------------------
# Pre-extraction
# ---------------------------------------------------------------------------


def _pre_extract(text: str) -> dict[str, object]:
    """Run all deterministic extractors and return a dict of field → value | None."""
    rate, billing_unit = extract_hourly_rate(text)
    hours = extract_hours_billed(text, billing_unit)

    # Normalize daily rate: if billed per-day, store it as-is (hourly_rate_usd
    # field on Invoice stores per-day for demurrage; R011 handles the conversion)
    return {
        "invoice_number": extract_invoice_number(text),
        "bol_number": extract_bol_number(text),
        "container_number": extract_container_number(text),
        "invoice_date": extract_invoice_date(text),
        "charge_incurred_date": extract_charge_incurred_date(text),
        "free_time_start_date": extract_free_time_start(text),
        "free_time_end_date": extract_free_time_end(text),
        "hours_billed": hours,
        "hourly_rate_usd": rate,
        "total_amount_usd": extract_total_amount(text),
        "tariff_number": extract_tariff_number(text),
        "published_free_time_days": extract_free_time_days(text),
    }


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------


class _InvoiceLLMOutput(BaseModel):
    """Internal structured-output model for the LLM extraction call.

    Mirrors Invoice but with all fields optional so the LLM can express
    "I did not find this" without triggering a validation error.  The
    assembler merges this with pre-extracted fields and constructs Invoice.
    """

    model_config = ConfigDict(extra="forbid")

    invoice_number: str | None = None
    carrier_name: str | None = None
    bol_number: str | None = None
    container_number: str | None = None
    charge_type: Literal["detention", "demurrage", "tonu", "layover", "lumper", "other"] | None = None
    charge_incurred_date: str | None = None   # YYYY-MM-DD string from LLM
    invoice_date: str | None = None           # YYYY-MM-DD string from LLM
    free_time_start: str | None = None        # ISO 8601 datetime string
    free_time_end: str | None = None          # ISO 8601 datetime string
    hours_billed: float | None = None
    hourly_rate_usd: float | None = None
    total_amount_usd: float | None = None
    billed_to_party: str | None = None
    basis_for_charge: str | None = None


def _invoke_llm(text: str, pre: dict[str, object]) -> _InvoiceLLMOutput:
    """Call the LLM with the pre-extracted context and document text."""
    llm = get_llm_client(purpose="paper_trail_extraction", model=_MODEL_NAME, temperature=0.0)
    chain = EXTRACTION_PROMPT | llm.with_structured_output(_InvoiceLLMOutput)
    context_str = build_pre_extracted_context({
        k: v for k, v in pre.items()
        if k not in ("tariff_number", "published_free_time_days",
                     "free_time_start_date", "free_time_end_date")
    })
    result = chain.invoke({
        "pre_extracted_context": context_str,
        "document_text": text[:6000],  # guard against token overflow on large docs
    })
    return result


# ---------------------------------------------------------------------------
# Assembly — merge pre-extracted + LLM output into Invoice + EvidencePackage
# ---------------------------------------------------------------------------


def _parse_date_str(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _parse_datetime_str(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _assemble_invoice(pre: dict[str, object], llm: _InvoiceLLMOutput) -> Invoice:
    """Merge pre-extracted and LLM outputs into a validated Invoice.

    LLM value wins when both are present (the LLM cross-checked and may
    have corrected the regex result).  Pre-extracted value is the fallback.
    Disagreements between regex and LLM are logged at WARNING level.
    """
    def _pick(llm_val: object, pre_val: object, field: str) -> object:
        if llm_val is not None and pre_val is not None and str(llm_val) != str(pre_val):
            logger.warning(
                "PaperTrail: LLM corrected pre-extracted %s: %r → %r (LLM wins)",
                field, pre_val, llm_val,
            )
        return llm_val if llm_val is not None else pre_val

    if not llm.carrier_name:
        logger.warning("PaperTrail: LLM returned null for required field 'carrier_name'")
    if not llm.billed_to_party:
        logger.warning("PaperTrail: LLM returned null for required field 'billed_to_party'")

    invoice_date = _parse_date_str(llm.invoice_date) or pre.get("invoice_date")
    charge_incurred = _parse_date_str(llm.charge_incurred_date) or pre.get("charge_incurred_date")

    def _dt_from_llm_or_date(llm_str: str | None, pre_date: date | None) -> datetime | None:
        if llm_str:
            return _parse_datetime_str(llm_str)
        if pre_date:
            return datetime(pre_date.year, pre_date.month, pre_date.day, 8, 0, tzinfo=timezone.utc)
        return None

    free_time_start = _dt_from_llm_or_date(
        llm.free_time_start, pre.get("free_time_start_date")  # type: ignore[arg-type]
    )
    free_time_end = _dt_from_llm_or_date(
        llm.free_time_end, pre.get("free_time_end_date")  # type: ignore[arg-type]
    )

    inv_num = _pick(llm.invoice_number, pre.get("invoice_number"), "invoice_number")
    bol = _pick(llm.bol_number, pre.get("bol_number"), "bol_number")
    ctr = _pick(llm.container_number, pre.get("container_number"), "container_number")
    hrs = _pick(llm.hours_billed, pre.get("hours_billed"), "hours_billed")
    rate = _pick(llm.hourly_rate_usd, pre.get("hourly_rate_usd"), "hourly_rate_usd")
    total = _pick(llm.total_amount_usd, pre.get("total_amount_usd"), "total_amount_usd")

    return Invoice(
        invoice_number=str(inv_num or "UNKNOWN"),
        carrier_name=str(llm.carrier_name or "Unknown Carrier"),
        bol_number=str(bol) if bol else None,
        container_number=str(ctr) if ctr else None,
        charge_type=llm.charge_type or "other",
        charge_incurred_date=charge_incurred or date.today(),
        invoice_date=invoice_date or date.today(),
        free_time_start=free_time_start,
        free_time_end=free_time_end,
        hours_billed=float(hrs or 0.0),
        hourly_rate_usd=float(rate or 0.0),
        total_amount_usd=float(total or 0.0),
        billed_to_party=str(llm.billed_to_party or "Unknown"),
        basis_for_charge=llm.basis_for_charge,
    )


def _assemble_evidence(
    pre: dict[str, object],
    llm: _InvoiceLLMOutput,
) -> EvidencePackage:
    """Build EvidencePackage from document-sourced fields only.

    Populates: carrier_tariff_reference, published_free_time_days,
    shipper_name.  All other fields stay None — populated by GateHarvester
    and CarrierBridge during Maestro Case execution.
    """
    tariff_ref: TariffReference | None = None
    tariff_number = pre.get("tariff_number")
    if tariff_number:
        tariff_ref = TariffReference(tariff_number=str(tariff_number))

    pub_free_days = pre.get("published_free_time_days")

    return EvidencePackage(
        carrier_tariff_reference=tariff_ref,
        published_free_time_days=float(pub_free_days) if pub_free_days is not None else None,
        shipper_name=llm.billed_to_party,
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def extract_case(
    source: PdfSource | EmailSource | EdiSource,
    filing_date: date | None = None,
    case_id_override: str | None = None,
) -> DisputeCase:
    """Extract a structured DisputeCase from a freight invoice document.

    Dispatches to the appropriate text extractor based on source.kind,
    runs deterministic regex pre-extraction, then calls the LLM with
    "confirm or correct" context for unstructured fields.

    Args:
        source: One of PdfSource, EmailSource, or EdiSource.  EdiSource
            raises NotImplementedError (v2 deferred).
        filing_date: Passed through for downstream DemandSmith use.
            Not stored in DisputeCase; provided for caller context.
        case_id_override: If provided, used as case_id directly.  Use in
            tests to match fixture case IDs exactly.

    Returns:
        A fully validated DisputeCase with Invoice and partial EvidencePackage.

    Raises:
        NotImplementedError: For EdiSource (v2 deferred).
        ValueError: If document text cannot be extracted or LLM output fails
            to produce a valid Invoice.
    """
    if isinstance(source, EdiSource):
        raise NotImplementedError(
            "EDI 210 parsing is deferred to v2.  The X12 ISA/IEA envelope "
            "and ST/SE 210 transaction set parser will be implemented in "
            "agents/paper_trail/extractors/edi.py in the next build phase."
        )

    logger.info("PaperTrail: extracting from source kind=%s", source.kind)

    if isinstance(source, PdfSource):
        text = extract_text_from_pdf(source.content)
    else:
        text = extract_text_from_email(source.raw_message)

    pre = _pre_extract(text)
    logger.info(
        "PaperTrail: pre-extracted %d/%d fields",
        sum(1 for v in pre.values() if v is not None),
        len(pre),
    )

    llm_output = _invoke_llm(text, pre)
    invoice = _assemble_invoice(pre, llm_output)
    evidence = _assemble_evidence(pre, llm_output)

    case_id = _generate_case_id(invoice.carrier_name, invoice.invoice_number, case_id_override)

    logger.info(
        "PaperTrail: extracted case_id=%s  carrier=%s  invoice=%s  amount=%.2f",
        case_id, invoice.carrier_name, invoice.invoice_number, invoice.total_amount_usd,
    )

    return DisputeCase(
        case_id=case_id,
        invoice=invoice,
        evidence=evidence,
    )
