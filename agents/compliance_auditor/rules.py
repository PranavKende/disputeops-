"""FMC Part 541 compliance rule functions for ComplianceAuditor.

Each public function evaluates one rule and returns a ``RuleResult``.
Rules are grouped into three sections:

  Section 1 — Deterministic (R001, R009, R010, R011, R012)
  Section 2 — Mixed: deterministic-first with LLM fallback (R002–R005, R008)
  Section 3 — LLM-assisted (R006, R007)

Section 3 is implemented in Phase D.

Engineering contract:
  - Every function signature: ``evaluate_RXXX(case: DisputeCase) -> RuleResult``
  - ``citation`` field: ``get_citation(rule_id).verbatim_text`` only —
    never supporting_authorities text, never hardcoded strings
  - ``evidence_refs``: "invoice.field" or "evidence.field" dotted paths
  - ``confidence``: 1.0 for clear deterministic outcomes; 0.95 at exact
    boundaries (day-30, exact-tolerance math); [0.6, 0.95] for LLM rules
  - ``violated = None`` only when prerequisite data is genuinely absent;
    populate ``missing_evidence`` with at least one ``MissingEvidenceItem``
  - ``logger.info`` on entry, ``logger.debug`` for sub-conditions,
    ``logger.warning`` for anomalies

Reuse decision (_has_dispute_disclosure vs _party_from_bol_deterministic):
  These two helpers solve different problems and are intentionally kept separate.
  ``_has_dispute_disclosure`` (R012) uses URL/contact *regex* to detect the
  *presence* of structured markers in free text.  ``_party_from_bol_deterministic``
  (R003) uses case-insensitive *substring containment* to match a known party
  name against BOL prose.  The former searches for patterns; the latter matches
  a specific known string.  Abstracting them into a shared ``_scan_text`` helper
  would merge two conceptually distinct operations and obscure each rule's intent.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from data.schemas.case import DisputeCase
from data.schemas.verdict import MissingEvidenceItem, RuleResult
from agents.compliance_auditor.citations import get_citation
from agents.shared.llm_client import get_llm_client

logger = logging.getLogger(__name__)

# Confidence constants — used across all three sections.
_CONF_CLEAR: float = 1.0
_CONF_BOUNDARY: float = 0.95   # exact boundary values; slight timezone/drafting ambiguity

# Regex patterns for R012 deterministic disclosure scan.
_URL_RE = re.compile(r"https?://|www\.|qr\s*code|qr\.", re.IGNORECASE)
_CONTACT_RE = re.compile(
    r"@"                             # email address marker
    r"|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"  # US phone format
    r"|tel:|email:|phone:|contact:",
    re.IGNORECASE,
)


# ===========================================================================
# SECTION 1 — DETERMINISTIC RULES  (pure Python, no LLM)
# R001, R009, R010, R011, R012
# ===========================================================================

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _window_days(start: date, end: date) -> int:
    """Calendar-day difference ``(end - start).days``."""
    return (end - start).days


def _check_calculation(hours: float, rate: float, total: float, tol: float = 0.01) -> bool:
    """Return ``True`` when ``hours × rate`` is within ``tol`` of ``total``."""
    return abs(hours * rate - total) <= tol


def _free_time_consumed_hours(case: DisputeCase) -> float | None:
    """Return hours of free time granted on the invoice, or ``None`` if fields absent."""
    inv = case.invoice
    if inv.free_time_start is None or inv.free_time_end is None:
        return None
    return (inv.free_time_end - inv.free_time_start).total_seconds() / 3600.0


def _has_dispute_disclosure(text: str) -> bool:
    """Return ``True`` when *text* contains a URL or contact-information marker."""
    return bool(_URL_RE.search(text) or _CONTACT_RE.search(text))


# ---------------------------------------------------------------------------
# R001 — Invoicing window  §541.7(a)
# ---------------------------------------------------------------------------

def evaluate_R001(case: DisputeCase) -> RuleResult:
    """Evaluate R001: invoice must be issued within 30 calendar days of charge.

    Deterministic — no LLM call.  Both ``invoice_date`` and
    ``charge_incurred_date`` are required ``Invoice`` fields, so this rule
    never returns ``violated = None``.

    Args:
        case: The ``DisputeCase`` under audit.

    Returns:
        A ``RuleResult`` with:
          - ``violated = True``  when delta > 30 days
          - ``violated = False`` when delta ≤ 30 days
          - ``confidence = 0.95`` at exactly 30 days (boundary uncertainty);
            ``1.0`` otherwise

    Example:
        >>> result = evaluate_R001(case)   # invoice_date 47 days after charge
        >>> result.violated
        True
        >>> result.confidence
        1.0
    """
    rule_id = "R001"
    logger.info("Evaluating %s (Invoicing window) for case %s", rule_id, case.case_id)

    inv = case.invoice
    delta = _window_days(inv.charge_incurred_date, inv.invoice_date)
    logger.debug("R001 delta=%d days (threshold=30)", delta)

    violated = delta > 30
    confidence = _CONF_BOUNDARY if delta == 30 else _CONF_CLEAR
    boundary_note = " (Boundary: exactly 30 days — confidence reduced.)" if delta == 30 else ""

    return RuleResult(
        rule_id=rule_id,
        rule_name="Invoicing window",
        violated=violated,
        confidence=confidence,
        evidence_refs=["invoice.invoice_date", "invoice.charge_incurred_date"],
        reasoning=(
            f"Invoice date {inv.invoice_date} is {delta} calendar day(s) after "
            f"charge incurred date {inv.charge_incurred_date}. "
            f"§541.7(a) requires invoicing within 30 calendar days. "
            f"{'VIOLATION: exceeds' if violated else 'Compliant: within'} the window."
            f"{boundary_note}"
        ),
        citation=get_citation(rule_id).verbatim_text,
    )


# ---------------------------------------------------------------------------
# R009 — Charge calculation  derived §541.6(c)
# ---------------------------------------------------------------------------

def evaluate_R009(case: DisputeCase) -> RuleResult:
    """Evaluate R009: hours_billed × hourly_rate_usd must equal total_amount_usd.

    Deterministic — no LLM call.  Tolerance is $0.01 to absorb rounding.
    All three fields are required on ``Invoice``, so ``violated = None``
    is not possible for this rule.

    Args:
        case: The ``DisputeCase`` under audit.

    Returns:
        ``violated = True`` when ``|hours × rate − total| > $0.01``.

    Example:
        >>> result = evaluate_R009(case)   # 8h × $75 = $600 but invoice shows $650
        >>> result.violated
        True
    """
    rule_id = "R009"
    logger.info("Evaluating %s (Charge calculation) for case %s", rule_id, case.case_id)

    inv = case.invoice
    expected = inv.hours_billed * inv.hourly_rate_usd
    delta = abs(expected - inv.total_amount_usd)
    logger.debug("R009 expected=%.2f actual=%.2f delta=%.4f", expected, inv.total_amount_usd, delta)

    violated = not _check_calculation(inv.hours_billed, inv.hourly_rate_usd, inv.total_amount_usd)

    return RuleResult(
        rule_id=rule_id,
        rule_name="Charge calculation",
        violated=violated,
        confidence=_CONF_CLEAR,
        evidence_refs=[
            "invoice.hours_billed",
            "invoice.hourly_rate_usd",
            "invoice.total_amount_usd",
        ],
        reasoning=(
            f"{inv.hours_billed}h × ${inv.hourly_rate_usd}/h = ${expected:.2f}; "
            f"invoice states ${inv.total_amount_usd:.2f} (delta ${delta:.4f}). "
            f"{'VIOLATION: delta exceeds $0.01 tolerance.' if violated else 'Compliant: within $0.01 tolerance.'}"
        ),
        citation=get_citation(rule_id).verbatim_text,
    )


# ---------------------------------------------------------------------------
# R010 — Free time consumption  §541.6(b)
# ---------------------------------------------------------------------------

def evaluate_R010(case: DisputeCase) -> RuleResult:
    """Evaluate R010: billing must not start before free time is fully consumed.

    Deterministic — no LLM call.  Requires ``invoice.free_time_end``;
    returns ``violated = None`` when that field is absent.

    The comparison is ``charge_incurred_date < free_time_end.date()``.
    Billing starting on the same calendar day free time ends is treated
    as compliant (free time was exhausted on that day).

    Args:
        case: The ``DisputeCase`` under audit.

    Returns:
        ``violated = True`` when billing started before free time expired.

    Example:
        >>> # free_time_end = 2024-03-05T18:00, charge_incurred = 2024-03-04
        >>> result = evaluate_R010(case)
        >>> result.violated
        True
    """
    rule_id = "R010"
    logger.info("Evaluating %s (Free time consumption) for case %s", rule_id, case.case_id)

    inv = case.invoice
    if inv.free_time_end is None:
        logger.debug("R010 cannot evaluate: free_time_end is None")
        return RuleResult(
            rule_id=rule_id,
            rule_name="Free time consumption",
            violated=None,
            confidence=0.0,
            evidence_refs=["invoice.free_time_end"],
            reasoning="Cannot evaluate: invoice.free_time_end is absent.",
            citation=get_citation(rule_id).verbatim_text,
            missing_evidence=[
                MissingEvidenceItem(
                    field_path="invoice.free_time_end",
                    description=(
                        "Free time end datetime is required to verify whether "
                        "billing started after free time was fully consumed."
                    ),
                    can_be_fetched_from=["carrier_portal", "terminal_portal"],
                )
            ],
        )

    free_end_date = inv.free_time_end.date()
    violated = inv.charge_incurred_date < free_end_date
    logger.debug(
        "R010 charge_incurred=%s free_time_end=%s violated=%s",
        inv.charge_incurred_date, free_end_date, violated,
    )

    return RuleResult(
        rule_id=rule_id,
        rule_name="Free time consumption",
        violated=violated,
        confidence=_CONF_CLEAR,
        evidence_refs=["invoice.charge_incurred_date", "invoice.free_time_end"],
        reasoning=(
            f"Free time ended {free_end_date}; billing started "
            f"{inv.charge_incurred_date}. "
            f"{'VIOLATION: billing started before free time expired.'if violated else 'Compliant: free time fully consumed before billing.'}"
        ),
        citation=get_citation(rule_id).verbatim_text,
    )


# ---------------------------------------------------------------------------
# R011 — Per-day vs per-hour rule application  §541.6(c) primary
# ---------------------------------------------------------------------------

def evaluate_R011(case: DisputeCase) -> RuleResult:
    """Evaluate R011: demurrage must be billed per calendar day, not per hour.

    Deterministic — no LLM call.

    Rule applies **only** when ``charge_type == 'demurrage'``.  For all
    other charge types the rule returns a clean pass with explicit
    not-applicable reasoning — this is *not* ``cannot_evaluate``, because
    the rule was fully evaluated and the answer is definitively no violation.

    For demurrage cases: ``violated = True`` when ``hours_billed`` is not a
    clean multiple of 24 (``invoice.billed_unit_inferred == 'hourly'``),
    indicating the carrier applied per-hour rates to a per-day tariff.

    Args:
        case: The ``DisputeCase`` under audit.

    Returns:
        ``violated = True`` for demurrage with fractional-day billing;
        ``violated = False`` for demurrage with clean-day billing or any
        non-demurrage charge type.

    Example:
        >>> # demurrage, hours_billed=25.0 (not a multiple of 24)
        >>> result = evaluate_R011(case)
        >>> result.violated
        True
        >>> result.confidence
        1.0
    """
    rule_id = "R011"
    logger.info("Evaluating %s (Per-day vs per-hour) for case %s", rule_id, case.case_id)

    inv = case.invoice

    if inv.charge_type != "demurrage":
        logger.debug("R011 not applicable: charge_type=%r", inv.charge_type)
        return RuleResult(
            rule_id=rule_id,
            rule_name="Per-day vs per-hour rule application",
            violated=False,
            confidence=_CONF_CLEAR,
            evidence_refs=["invoice.charge_type"],
            reasoning=(
                f"Not applicable: charge_type is {inv.charge_type!r}; "
                "per-day rule applies only to demurrage."
            ),
            citation=get_citation(rule_id).verbatim_text,
        )

    unit = inv.billed_unit_inferred
    violated = unit == "hourly"
    logger.debug("R011 billed_unit_inferred=%r violated=%s", unit, violated)

    return RuleResult(
        rule_id=rule_id,
        rule_name="Per-day vs per-hour rule application",
        violated=violated,
        confidence=_CONF_CLEAR,
        evidence_refs=["invoice.charge_type", "invoice.hours_billed"],
        reasoning=(
            f"charge_type=demurrage; hours_billed={inv.hours_billed} "
            f"({inv.hours_billed / 24:.2f} days). "
            f"billed_unit_inferred={unit!r}. "
            f"{'VIOLATION: demurrage billed per-hour (fractional days) rather than per calendar day.'if violated else 'Compliant: hours_billed is a clean multiple of 24 (per-day billing).'}"
        ),
        citation=get_citation(rule_id).verbatim_text,
    )


# ---------------------------------------------------------------------------
# R012 — Dispute mechanism disclosure  §541.6(d)
# ---------------------------------------------------------------------------

def evaluate_R012(case: DisputeCase) -> RuleResult:
    """Evaluate R012: carrier must disclose dispute contact and URL on invoice.

    Deterministic — no LLM call.  Audits the **carrier's** §541.6(d)
    obligation, separate from R008's shipper-readiness check.

    Checks for URL-like and contact-information markers in the available
    invoice text fields (``invoice.basis_for_charge``,
    ``evidence.bol_terms``).

    Conservative implementation note: This deterministic check identifies
    clear absences (both text fields empty) but cannot detect *substantive*
    non-compliance (fields present but containing irrelevant text).  A v2
    enhancement would add an LLM fallback to evaluate whether the fields
    actually contain dispute-mechanism disclosures, not merely unrelated
    content.  Acceptable v1 trade-off: false negatives possible, false
    positives not.

    Args:
        case: The ``DisputeCase`` under audit.

    Returns:
        ``violated = None`` when no text is available to scan.
        ``violated = True``  when available text contains no URL or
            contact-information markers.
        ``violated = False`` when at least one marker is detected.

    Example:
        >>> # basis_for_charge contains "disputes@carrier.com"
        >>> result = evaluate_R012(case)
        >>> result.violated
        False
    """
    rule_id = "R012"
    logger.info("Evaluating %s (Dispute mechanism disclosure) for case %s", rule_id, case.case_id)

    texts: list[tuple[str, str]] = []  # (field_path, text)
    if case.invoice.basis_for_charge is not None:
        texts.append(("invoice.basis_for_charge", case.invoice.basis_for_charge))
    if case.evidence.bol_terms is not None:
        texts.append(("evidence.bol_terms", case.evidence.bol_terms))

    if not texts:
        logger.debug("R012 cannot evaluate: no text fields available")
        return RuleResult(
            rule_id=rule_id,
            rule_name="Dispute mechanism disclosure",
            violated=None,
            confidence=0.0,
            evidence_refs=[],
            reasoning=(
                "Cannot evaluate: both invoice.basis_for_charge and "
                "evidence.bol_terms are absent — no invoice text available "
                "to scan for §541.6(d) dispute-mechanism disclosures."
            ),
            citation=get_citation(rule_id).verbatim_text,
            missing_evidence=[
                MissingEvidenceItem(
                    field_path="invoice.basis_for_charge",
                    description=(
                        "Invoice narrative text needed to scan for §541.6(d) "
                        "contact information and URL/digital-means disclosure."
                    ),
                    can_be_fetched_from=["carrier_portal"],
                ),
                MissingEvidenceItem(
                    field_path="evidence.bol_terms",
                    description=(
                        "BOL terms text needed as fallback source for §541.6(d) "
                        "dispute-mechanism disclosures when invoice text is absent."
                    ),
                    can_be_fetched_from=["tms", "carrier_portal"],
                ),
            ],
        )

    combined = " ".join(t for _, t in texts)
    scanned_refs = [path for path, _ in texts]
    found = _has_dispute_disclosure(combined)
    violated = not found
    logger.debug("R012 scanned %d text field(s), disclosure_found=%s", len(texts), found)

    if violated:
        logger.warning(
            "R012 potential violation for case %s: no URL or contact marker found "
            "in available invoice text.", case.case_id,
        )

    return RuleResult(
        rule_id=rule_id,
        rule_name="Dispute mechanism disclosure",
        violated=violated,
        confidence=_CONF_CLEAR,
        evidence_refs=scanned_refs,
        reasoning=(
            f"Scanned {len(texts)} text field(s) "
            f"({', '.join(scanned_refs)}) for §541.6(d) URL/contact markers. "
            f"{'VIOLATION: no dispute contact or URL/digital-means detected.'if violated else 'Compliant: dispute contact or URL/digital-means marker detected.'}"
        ),
        citation=get_citation(rule_id).verbatim_text,
    )


# ===========================================================================
# SECTION 2 — MIXED RULES  (deterministic-first, LLM fallback)
# R002, R003, R004, R005, R008
# ===========================================================================

# ---------------------------------------------------------------------------
# Shared Section 2 infrastructure
# ---------------------------------------------------------------------------

class _LLMAssessment(BaseModel):
    """Minimal structured output returned by LLM fallback calls.

    The rule function assembles the full ``RuleResult`` from this plus
    the static fields (rule_id, rule_name, citation, evidence_refs).
    Keeping the LLM schema small reduces hallucination surface area.
    """

    violated: bool | None = Field(
        ...,
        description="True=violation, False=compliant, null=cannot determine",
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="Confidence in the determination [0–1]; will be clamped to [0.6, 0.95]",
    )
    reasoning: str = Field(
        ...,
        description="1–3 sentence plain-English explanation for the demand letter",
    )


def _clamp_confidence(conf: float, lo: float = 0.6, hi: float = 0.95) -> float:
    """Clamp LLM-reported confidence to [lo, hi].

    LLM judgments should never claim 1.0 deterministic certainty.
    Values below 0.6 indicate the model is too uncertain to be useful —
    the rule should return cannot_evaluate instead.
    """
    return max(lo, min(hi, conf))


def _make_missing(field_path: str, description: str,
                  sources: list[str] | None = None) -> MissingEvidenceItem:
    """Shorthand constructor for ``MissingEvidenceItem``."""
    return MissingEvidenceItem(
        field_path=field_path,
        description=description,
        can_be_fetched_from=sources,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# R002 — Required minimum content  §541.6(a)–(c) + §541.5
# ---------------------------------------------------------------------------

_R002_REQUIRED_NULLABLE: list[tuple[str, str]] = [
    ("bol_number",        "invoice.bol_number"),
    ("container_number",  "invoice.container_number"),
    ("free_time_start",   "invoice.free_time_start"),
    ("free_time_end",     "invoice.free_time_end"),
    ("basis_for_charge",  "invoice.basis_for_charge"),
]
_R002_GENERIC_PHRASES = frozenset({
    "detention charge", "demurrage charge", "per tariff", "standard charge",
    "standard rate", "per contract", "per bl", "n/a", "na",
})


def _try_deterministic_R002(case: DisputeCase) -> RuleResult | None:
    """Check for clearly missing §541.6 required fields.

    Returns ``RuleResult`` (violated=True) if any field is None.
    Returns ``RuleResult`` (violated=False) if all fields are present and
    ``basis_for_charge`` is substantive (≥ 10 chars, not a known generic phrase).
    Returns ``None`` to signal LLM fallback when ``basis_for_charge`` is
    present but suspiciously short or generic.
    """
    missing = [
        (attr, path)
        for attr, path in _R002_REQUIRED_NULLABLE
        if getattr(case.invoice, attr) is None
    ]
    if missing:
        missing_items = [
            _make_missing(path, f"§541.6 required field '{attr}' is absent from invoice.",
                          ["carrier_portal"])
            for attr, path in missing
        ]
        return RuleResult(
            rule_id="R002", rule_name="Required minimum content",
            violated=True, confidence=_CONF_CLEAR,
            evidence_refs=[p for _, p in missing],
            reasoning=(
                f"Invoice is missing {len(missing)} required §541.6 field(s): "
                f"{', '.join(p for _, p in missing)}. "
                "Per §541.5, failure to include required information eliminates "
                "the obligation to pay."
            ),
            citation=get_citation("R002").verbatim_text,
            missing_evidence=missing_items,
        )

    bfc = (case.invoice.basis_for_charge or "").strip()
    bfc_lower = bfc.lower()
    # Full-string exact match catches very short tokens like "N/A", "na".
    # Substring match (min phrase length 5) catches multi-word boilerplate
    # like "demurrage charge" embedded in a longer string.
    is_generic = (
        len(bfc) < 10
        or bfc_lower in _R002_GENERIC_PHRASES
        or any(
            phrase in bfc_lower
            for phrase in _R002_GENERIC_PHRASES
            if len(phrase) >= 5
        )
    )
    if not is_generic:
        return RuleResult(
            rule_id="R002", rule_name="Required minimum content",
            violated=False, confidence=_CONF_CLEAR,
            evidence_refs=[p for _, p in _R002_REQUIRED_NULLABLE],
            reasoning=(
                "All required §541.6(a)–(c) fields are present and "
                "basis_for_charge is substantive."
            ),
            citation=get_citation("R002").verbatim_text,
        )
    return None  # ambiguous — hand off to LLM


def _evaluate_R002_via_llm(case: DisputeCase) -> RuleResult:
    """LLM evaluation of basis_for_charge quality for R002."""
    from agents.compliance_auditor.prompts import R002_BASIS_QUALITY_PROMPT
    inv = case.invoice
    llm = get_llm_client(purpose="R002_basis_quality")
    chain = llm.with_structured_output(_LLMAssessment)
    assessment: _LLMAssessment = chain.invoke(
        R002_BASIS_QUALITY_PROMPT.format_messages(
            basis_for_charge=inv.basis_for_charge,
            carrier_name=inv.carrier_name,
            charge_type=inv.charge_type,
            billed_to_party=inv.billed_to_party,
        )
    )
    return RuleResult(
        rule_id="R002", rule_name="Required minimum content",
        violated=assessment.violated,
        confidence=_clamp_confidence(assessment.confidence),
        evidence_refs=["invoice.basis_for_charge"],
        reasoning=assessment.reasoning,
        citation=get_citation("R002").verbatim_text,
    )


def evaluate_R002(case: DisputeCase) -> RuleResult:
    """Evaluate R002: invoice must contain all required §541.6 minimum content.

    Mixed rule. Deterministic path catches clearly missing fields (None values).
    LLM fallback evaluates whether a present-but-short ``basis_for_charge``
    is substantively compliant with §541.6(a)(4)'s liability-basis requirement.

    Args:
        case: The ``DisputeCase`` under audit.

    Returns:
        ``violated=True``  — one or more required fields are absent (det.).
        ``violated=False`` — all fields present and basis is substantive (det.
                            or LLM).
        ``violated=None``  — not possible; all required fields either present
                            or clearly missing.

    Example:
        >>> # invoice.bol_number is None
        >>> evaluate_R002(case).violated
        True
    """
    rule_id = "R002"
    logger.info("Evaluating %s (Required minimum content) for case %s", rule_id, case.case_id)
    det = _try_deterministic_R002(case)
    if det is not None:
        logger.debug("R002 resolved deterministically: violated=%s", det.violated)
        return det
    logger.info("R002 falling back to LLM: basis_for_charge is short/generic")
    return _evaluate_R002_via_llm(case)


# ---------------------------------------------------------------------------
# R003 — Correct billing party  §541.4
# ---------------------------------------------------------------------------

def _party_from_bol_deterministic(billed_to: str, bol_terms: str | None,
                                   shipper_name: str | None) -> bool | None:
    """Check billed party against known-party references.

    Returns ``True`` (match found — no violation), ``False`` (clear mismatch
    against shipper_name with no BOL to check), or ``None`` (ambiguous —
    BOL text present but simple match failed; hand off to LLM).

    Note: §541.4 allows billing to either the contracting party OR the
    consignee.  A mismatch with shipper_name alone is not conclusive because
    the billed party may be the consignee.  That distinction requires BOL
    parsing via LLM fallback.
    """
    normalise = lambda s: s.strip().lower()
    bt = normalise(billed_to)

    if shipper_name and normalise(shipper_name) == bt:
        return True  # exact match with known contracting party

    if bol_terms:
        if bt in normalise(bol_terms):
            return True  # billed party found in BOL text — likely valid
        return None  # BOL present but no match; LLM should parse parties

    # No BOL and shipper_name either absent or mismatched
    if shipper_name and normalise(shipper_name) != bt:
        return False  # clear mismatch against the only known reference
    return None  # no reference data at all — cannot determine


def _evaluate_R003_via_llm(case: DisputeCase) -> RuleResult:
    """LLM evaluation of billing-party validity from BOL text for R003."""
    from agents.compliance_auditor.prompts import R003_BILLING_PARTY_FALLBACK_PROMPT
    llm = get_llm_client(purpose="R003_billing_party")
    chain = llm.with_structured_output(_LLMAssessment)
    assessment: _LLMAssessment = chain.invoke(
        R003_BILLING_PARTY_FALLBACK_PROMPT.format_messages(
            bol_terms=case.evidence.bol_terms,
            billed_to_party=case.invoice.billed_to_party,
        )
    )
    return RuleResult(
        rule_id="R003", rule_name="Correct billing party",
        violated=assessment.violated,
        confidence=_clamp_confidence(assessment.confidence),
        evidence_refs=["invoice.billed_to_party", "evidence.bol_terms"],
        reasoning=assessment.reasoning,
        citation=get_citation("R003").verbatim_text,
    )


def evaluate_R003(case: DisputeCase) -> RuleResult:
    """Evaluate R003: invoice must be sent to the legally responsible party.

    Mixed rule. Deterministic path checks ``billed_to_party`` against
    ``evidence.shipper_name`` (exact) and ``evidence.bol_terms`` (substring).
    LLM fallback parses BOL prose to identify contracting party and consignee
    when simple substring matching is inconclusive.

    Args:
        case: The ``DisputeCase`` under audit.

    Returns:
        ``violated=True``  — billed party clearly does not match known references.
        ``violated=False`` — billed party matches shipper name or appears in BOL.
        ``violated=None``  — no shipper name and no BOL text available.

    Example:
        >>> # billed_to_party matches evidence.shipper_name exactly
        >>> evaluate_R003(case).violated
        False
    """
    rule_id = "R003"
    logger.info("Evaluating %s (Correct billing party) for case %s", rule_id, case.case_id)
    inv, evid = case.invoice, case.evidence

    det = _party_from_bol_deterministic(inv.billed_to_party, evid.bol_terms, evid.shipper_name)
    logger.debug("R003 deterministic result: %s", det)

    if det is True:
        return RuleResult(
            rule_id=rule_id, rule_name="Correct billing party",
            violated=False, confidence=_CONF_CLEAR,
            evidence_refs=["invoice.billed_to_party", "evidence.shipper_name"],
            reasoning=(
                f"Billed party {inv.billed_to_party!r} matches the contracting "
                "party or appears in BOL terms — §541.4 satisfied."
            ),
            citation=get_citation(rule_id).verbatim_text,
        )

    if det is False:
        return RuleResult(
            rule_id=rule_id, rule_name="Correct billing party",
            violated=True, confidence=_CONF_BOUNDARY,
            evidence_refs=["invoice.billed_to_party", "evidence.shipper_name"],
            reasoning=(
                f"Billed party {inv.billed_to_party!r} does not match shipper "
                f"{evid.shipper_name!r} and no BOL text is available to identify "
                "an alternative valid party (consignee). §541.4 likely violated."
            ),
            citation=get_citation(rule_id).verbatim_text,
        )

    # det is None — two possible paths
    if evid.bol_terms is not None:
        logger.info("R003 falling back to LLM: BOL present but no simple match")
        return _evaluate_R003_via_llm(case)

    # No BOL, no shipper_name — truly cannot evaluate
    return RuleResult(
        rule_id=rule_id, rule_name="Correct billing party",
        violated=None, confidence=0.0,
        evidence_refs=[],
        reasoning="Cannot evaluate: no shipper_name and no bol_terms available.",
        citation=get_citation(rule_id).verbatim_text,
        missing_evidence=[
            _make_missing("evidence.shipper_name",
                          "Shipper name from BOL needed to verify billing party.",
                          ["tms", "carrier_portal"]),
            _make_missing("evidence.bol_terms",
                          "BOL text needed to identify contracting party and consignee.",
                          ["tms", "carrier_portal"]),
        ],
    )


# ---------------------------------------------------------------------------
# R004 — Free time accuracy  §541.6(b) primary, §541.6(c) supporting
# ---------------------------------------------------------------------------

_R004_TOLERANCE_HOURS: float = 0.5  # half-hour tolerance for free-time comparison


def _claimed_free_time_hours(case: DisputeCase) -> float | None:
    """Invoice-implied free time in hours, or None if fields absent."""
    inv = case.invoice
    if inv.free_time_start is None or inv.free_time_end is None:
        return None
    return (inv.free_time_end - inv.free_time_start).total_seconds() / 3600.0


def _try_deterministic_R004(case: DisputeCase) -> RuleResult | None:
    """Compare invoice free time to TariffReference.published_free_time_hours.

    Returns RuleResult if tariff data is available; None otherwise.
    """
    evid = case.evidence
    tariff = evid.carrier_tariff_reference
    if tariff is None or tariff.published_free_time_hours is None:
        return None  # no tariff data — try LLM on BOL

    claimed = _claimed_free_time_hours(case)
    if claimed is None:
        return None  # can't compute claimed — also fall through

    published = tariff.published_free_time_hours
    delta = abs(claimed - published)
    violated = delta > _R004_TOLERANCE_HOURS
    logger.debug("R004 claimed=%.1fh published=%.1fh delta=%.2fh violated=%s",
                 claimed, published, delta, violated)
    return RuleResult(
        rule_id="R004", rule_name="Free time accuracy",
        violated=violated, confidence=_CONF_CLEAR,
        evidence_refs=[
            "invoice.free_time_start", "invoice.free_time_end",
            "evidence.carrier_tariff_reference",
        ],
        reasoning=(
            f"Invoice claims {claimed:.1f}h free time; tariff {tariff.tariff_number} "
            f"publishes {published:.1f}h (delta {delta:.2f}h, tolerance "
            f"{_R004_TOLERANCE_HOURS}h). "
            f"{'VIOLATION: claimed free time understates published tariff.' if violated else 'Compliant: free time matches published tariff.'}"
        ),
        citation=get_citation("R004").verbatim_text,
    )


def _evaluate_R004_via_llm(case: DisputeCase) -> RuleResult:
    """LLM scans BOL text for contractual free-time language for R004."""
    from agents.compliance_auditor.prompts import R004_FREE_TIME_BOL_SCAN_PROMPT
    claimed = _claimed_free_time_hours(case)
    llm = get_llm_client(purpose="R004_free_time_bol_scan")
    chain = llm.with_structured_output(_LLMAssessment)
    assessment: _LLMAssessment = chain.invoke(
        R004_FREE_TIME_BOL_SCAN_PROMPT.format_messages(
            bol_terms=case.evidence.bol_terms,
            free_time_start=case.invoice.free_time_start,
            free_time_end=case.invoice.free_time_end,
            claimed_free_time_hours=claimed or 0.0,
        )
    )
    return RuleResult(
        rule_id="R004", rule_name="Free time accuracy",
        violated=assessment.violated,
        confidence=_clamp_confidence(assessment.confidence),
        evidence_refs=["invoice.free_time_start", "invoice.free_time_end", "evidence.bol_terms"],
        reasoning=assessment.reasoning,
        citation=get_citation("R004").verbatim_text,
    )


def evaluate_R004(case: DisputeCase) -> RuleResult:
    """Evaluate R004: invoice free time must match carrier's published tariff.

    Mixed rule. Deterministic path compares invoice free-time window to
    ``TariffReference.published_free_time_hours``. LLM fallback scans
    ``evidence.bol_terms`` for contractual free-time language when the
    tariff reference is absent.

    Args:
        case: The ``DisputeCase`` under audit.

    Returns:
        ``violated=True``  — claimed free time deviates > 0.5h from tariff.
        ``violated=False`` — free time matches within tolerance.
        ``violated=None``  — no tariff reference and no BOL text.

    Example:
        >>> # tariff says 48h, invoice claims 24h
        >>> evaluate_R004(case).violated
        True
    """
    rule_id = "R004"
    logger.info("Evaluating %s (Free time accuracy) for case %s", rule_id, case.case_id)
    det = _try_deterministic_R004(case)
    if det is not None:
        logger.debug("R004 resolved deterministically: violated=%s", det.violated)
        return det

    if case.evidence.bol_terms is not None:
        logger.info("R004 falling back to LLM: no tariff reference, scanning BOL")
        return _evaluate_R004_via_llm(case)

    return RuleResult(
        rule_id=rule_id, rule_name="Free time accuracy",
        violated=None, confidence=0.0, evidence_refs=[],
        reasoning="Cannot evaluate: no tariff reference and no BOL text available.",
        citation=get_citation(rule_id).verbatim_text,
        missing_evidence=[
            _make_missing("evidence.carrier_tariff_reference",
                          "Tariff reference needed to verify published free time.",
                          ["carrier_portal"]),
            _make_missing("evidence.bol_terms",
                          "BOL text needed as fallback source for contractual free time.",
                          ["tms", "carrier_portal"]),
        ],
    )


# ---------------------------------------------------------------------------
# R005 — Gate timestamp consistency  Industry standard / §541.6(b)
# ---------------------------------------------------------------------------

_R005_TOLERANCE_MINUTES: float = 15.0


def _gate_delta_minutes(ts1: datetime, ts2: datetime) -> float:
    """Absolute difference between two datetimes in minutes."""
    return abs((ts1 - ts2).total_seconds()) / 60.0


def _try_deterministic_R005(case: DisputeCase) -> RuleResult | None:
    """Compare invoice timestamps to evidence gate timestamps within 15 min.

    Also checks typed ``terminal_records`` for gate_in/gate_out events.
    Returns None if no parseable timestamps are available.
    """
    inv, evid = case.invoice, case.evidence
    checks: list[tuple[str, str, float]] = []  # (invoice_label, evidence_label, delta_min)

    if inv.free_time_start and evid.gate_in_timestamp:
        delta = _gate_delta_minutes(inv.free_time_start, evid.gate_in_timestamp)
        checks.append(("invoice.free_time_start", "evidence.gate_in_timestamp", delta))
        logger.debug("R005 gate-in delta=%.1f min", delta)

    if inv.free_time_end and evid.gate_out_timestamp:
        delta = _gate_delta_minutes(inv.free_time_end, evid.gate_out_timestamp)
        checks.append(("invoice.free_time_end", "evidence.gate_out_timestamp", delta))
        logger.debug("R005 gate-out delta=%.1f min", delta)

    # Also check typed terminal_records
    for rec in evid.terminal_records:
        if rec.event_type == "gate_in" and inv.free_time_start:
            delta = _gate_delta_minutes(inv.free_time_start, rec.event_timestamp)
            checks.append((
                "invoice.free_time_start",
                f"evidence.terminal_records[{rec.record_id}].event_timestamp",
                delta,
            ))
        elif rec.event_type == "gate_out" and inv.free_time_end:
            delta = _gate_delta_minutes(inv.free_time_end, rec.event_timestamp)
            checks.append((
                "invoice.free_time_end",
                f"evidence.terminal_records[{rec.record_id}].event_timestamp",
                delta,
            ))

    if not checks:
        return None  # no comparable timestamps found

    over_tolerance = [(a, b, d) for a, b, d in checks if d > _R005_TOLERANCE_MINUTES]
    violated = bool(over_tolerance)
    evidence_refs = list({a for a, b, d in checks} | {b for a, b, d in checks})
    worst_delta = max(d for _, _, d in checks)

    return RuleResult(
        rule_id="R005", rule_name="Gate timestamp consistency",
        violated=violated, confidence=_CONF_CLEAR,
        evidence_refs=evidence_refs,
        reasoning=(
            f"Compared {len(checks)} timestamp pair(s); worst delta {worst_delta:.1f} min "
            f"(tolerance {_R005_TOLERANCE_MINUTES} min). "
            + (f"VIOLATION: {len(over_tolerance)} pair(s) exceed tolerance: "
               f"{[(a,b,f'{d:.1f}m') for a,b,d in over_tolerance]}."
               if violated else "Compliant: all pairs within tolerance.")
        ),
        citation=get_citation("R005").verbatim_text,
    )


def _has_notes_with_timestamps(case: DisputeCase) -> bool:
    """True if any TerminalRecord has a non-empty notes field."""
    return any(rec.notes for rec in case.evidence.terminal_records)


def _evaluate_R005_via_llm(case: DisputeCase) -> RuleResult:
    """LLM parses free-text terminal record notes for gate timestamps."""
    from agents.compliance_auditor.prompts import R005_TIMESTAMP_PARSE_PROMPT
    notes_combined = "\n---\n".join(
        f"[{rec.record_id} {rec.event_type}] {rec.notes}"
        for rec in case.evidence.terminal_records
        if rec.notes
    )
    llm = get_llm_client(purpose="R005_timestamp_parse")
    chain = llm.with_structured_output(_LLMAssessment)
    assessment: _LLMAssessment = chain.invoke(
        R005_TIMESTAMP_PARSE_PROMPT.format_messages(
            terminal_record_notes=notes_combined,
            invoice_free_time_start=case.invoice.free_time_start,
            invoice_free_time_end=case.invoice.free_time_end,
        )
    )
    return RuleResult(
        rule_id="R005", rule_name="Gate timestamp consistency",
        violated=assessment.violated,
        confidence=_clamp_confidence(assessment.confidence),
        evidence_refs=["invoice.free_time_start", "invoice.free_time_end",
                       "evidence.terminal_records[*].notes"],
        reasoning=assessment.reasoning,
        citation=get_citation("R005").verbatim_text,
    )


def evaluate_R005(case: DisputeCase) -> RuleResult:
    """Evaluate R005: gate timestamps must reconcile within 15-minute tolerance.

    Mixed rule. Deterministic path compares invoice free-time boundaries to
    ``evidence.gate_in_timestamp``, ``evidence.gate_out_timestamp``, and
    typed ``TerminalRecord`` gate events. LLM fallback extracts timestamps
    from ``TerminalRecord.notes`` when they are in free-text form. Returns
    ``violated=None`` when no gate timestamps are available at all.

    Args:
        case: The ``DisputeCase`` under audit.

    Example:
        >>> # gate_in_timestamp is 25 min before invoice free_time_start
        >>> evaluate_R005(case).violated
        True
    """
    rule_id = "R005"
    logger.info("Evaluating %s (Gate timestamp consistency) for case %s", rule_id, case.case_id)
    det = _try_deterministic_R005(case)
    if det is not None:
        logger.debug("R005 resolved deterministically: violated=%s", det.violated)
        return det

    if _has_notes_with_timestamps(case):
        logger.info("R005 falling back to LLM: timestamps in TerminalRecord notes")
        return _evaluate_R005_via_llm(case)

    return RuleResult(
        rule_id=rule_id, rule_name="Gate timestamp consistency",
        violated=None, confidence=0.0, evidence_refs=[],
        reasoning=(
            "Cannot evaluate: no gate timestamps in evidence.gate_in_timestamp, "
            "evidence.gate_out_timestamp, terminal_records, or terminal record notes."
        ),
        citation=get_citation(rule_id).verbatim_text,
        missing_evidence=[
            _make_missing("evidence.gate_in_timestamp",
                          "Gate-in time needed to verify invoice free_time_start.",
                          ["terminal_portal", "driver_log"]),
            _make_missing("evidence.gate_out_timestamp",
                          "Gate-out time needed to verify invoice free_time_end.",
                          ["terminal_portal", "driver_log"]),
        ],
    )


# ---------------------------------------------------------------------------
# R008 — Dispute window status  §541.8(a)
# ---------------------------------------------------------------------------

DisputeWindowStatus = Literal[
    "window_open_dispute_not_filed",
    "window_open_dispute_filed",
    "window_expired_no_dispute",
    "window_expired_dispute_filed",
]


def _compute_dispute_status(case: DisputeCase) -> DisputeWindowStatus:
    """Derive the four-state dispute-window status for R008.

    Day 30 from invoice_date is still within the §541.8(a) window
    ("at least thirty (30) calendar days"); the window expires on day 31.

    Args:
        case: The ``DisputeCase`` under audit.

    Returns:
        One of the four ``DisputeWindowStatus`` literal values.
    """
    today = date.today()
    days_elapsed = (today - case.invoice.invoice_date).days
    window_open = days_elapsed <= 30
    dispute_filed = case.evidence.dispute_notice_date is not None

    if window_open and not dispute_filed:
        return "window_open_dispute_not_filed"
    if window_open and dispute_filed:
        return "window_open_dispute_filed"
    if not window_open and not dispute_filed:
        return "window_expired_no_dispute"
    return "window_expired_dispute_filed"


def evaluate_R008(case: DisputeCase) -> RuleResult:
    """Evaluate R008: report shipper dispute-window status (never a violation).

    Always returns ``violated=False, confidence=1.0``.  Encodes one of four
    structured statuses in the ``reasoning`` field so RecoveryOracle can
    pattern-match without parsing free text.

    Statuses:
      - ``window_open_dispute_not_filed``   Dispute still timely; not yet filed.
      - ``window_open_dispute_filed``        Dispute in progress.
      - ``window_expired_no_dispute``        Window lapsed; no action taken.
      - ``window_expired_dispute_filed``     Dispute filed within the window.

    DemandSmith ignores R008 entirely (violated is always False).
    RecoveryOracle reads the ``reasoning`` field to extract the status token.

    Args:
        case: The ``DisputeCase`` under audit.

    Example:
        >>> result = evaluate_R008(case)
        >>> result.violated
        False
        >>> "window_open" in result.reasoning
        True
    """
    rule_id = "R008"
    logger.info("Evaluating %s (Dispute window status) for case %s", rule_id, case.case_id)

    status = _compute_dispute_status(case)
    today = date.today()
    days_elapsed = (today - case.invoice.invoice_date).days
    logger.debug("R008 status=%s days_elapsed=%d", status, days_elapsed)

    status_notes = {
        "window_open_dispute_not_filed":
            f"§541.8(a) window open ({days_elapsed}/30 days elapsed). "
            "Dispute not yet filed — action still timely.",
        "window_open_dispute_filed":
            f"§541.8(a) window open ({days_elapsed}/30 days elapsed). "
            f"Dispute filed {case.evidence.dispute_notice_date}.",
        "window_expired_no_dispute":
            f"§541.8(a) window expired ({days_elapsed} days elapsed). "
            "No dispute notice was filed.",
        "window_expired_dispute_filed":
            f"§541.8(a) window expired ({days_elapsed} days elapsed). "
            f"Dispute was filed {case.evidence.dispute_notice_date} — within window.",
    }

    return RuleResult(
        rule_id=rule_id, rule_name="Dispute notice period",
        violated=False, confidence=_CONF_CLEAR,
        evidence_refs=["invoice.invoice_date", "evidence.dispute_notice_date"],
        reasoning=f"STATUS:{status} — {status_notes[status]}",
        citation=get_citation(rule_id).verbatim_text,
    )


# ===========================================================================
# SECTION 3 — LLM-PRIMARY RULES
# R006 (Appointment compliance) and R007 (Force majeure events)
# No deterministic path.  Evidence gate → LLM evaluation.
# ===========================================================================

# ---------------------------------------------------------------------------
# R006 — Appointment compliance  §541.6(e)(2)
# ---------------------------------------------------------------------------

def _can_evaluate_R006(case: DisputeCase) -> bool:
    """True if at least one appointment-evidence source is present."""
    if case.evidence.appointment_time is not None:
        return True
    return any(
        r.event_type == "appointment_scheduled"
        for r in case.evidence.terminal_records
    )


def _cannot_evaluate_R006(case: DisputeCase) -> RuleResult:
    """Build a cannot_evaluate RuleResult when R006 lacks appointment evidence."""
    return RuleResult(
        rule_id="R006", rule_name="Appointment compliance",
        violated=None, confidence=0.0, evidence_refs=[],
        reasoning=(
            "Cannot evaluate: no appointment_time and no terminal_records of "
            "type 'appointment_scheduled' are present."
        ),
        citation=get_citation("R006").verbatim_text,
        missing_evidence=[
            _make_missing(
                "evidence.appointment_time",
                "Scheduled appointment time needed to assess arrival compliance.",
                ["tms", "carrier_portal"],
            ),
            _make_missing(
                "evidence.terminal_records[appointment_scheduled]",
                "Terminal appointment record needed as alternative appointment source.",
                ["terminal_portal"],
            ),
        ],
    )


def _evaluate_R006_via_llm(case: DisputeCase) -> RuleResult:
    """LLM evaluates whether the appointment was honoured and who caused any delay."""
    from agents.compliance_auditor.prompts import R006_APPOINTMENT_PROMPT

    inv, evid = case.invoice, case.evidence

    appt_records = [
        r for r in evid.terminal_records
        if r.event_type == "appointment_scheduled"
    ]
    evidence_summary = (
        f"Invoice free-time window: {inv.free_time_start} to {inv.free_time_end}.\n"
        f"Scheduled appointment time: {evid.appointment_time or 'derived from terminal records'}.\n"
        f"Gate-in timestamp: {evid.gate_in_timestamp or 'not recorded'}.\n"
        f"Gate-out timestamp: {evid.gate_out_timestamp or 'not recorded'}.\n"
        f"Appointment terminal records: "
        + (
            "; ".join(
                f"[{r.record_id}] {r.event_type} at {r.event_timestamp}"
                + (f" — {r.notes}" if r.notes else "")
                for r in appt_records
            ) or "none"
        )
        + f"\nCarrier: {inv.carrier_name}. Billed party: {inv.billed_to_party}."
    )

    llm = get_llm_client(purpose="R006_appointment")
    chain = llm.with_structured_output(_LLMAssessment)
    assessment: _LLMAssessment = chain.invoke(
        R006_APPOINTMENT_PROMPT.format_messages(
            citation=get_citation("R006").verbatim_text,
            evidence_summary=evidence_summary,
        )
    )

    raw_conf = assessment.confidence
    if raw_conf < 0.6:
        logger.info(
            "R006 LLM confidence %.2f below 0.6 threshold — returning cannot_evaluate",
            raw_conf,
        )
        return RuleResult(
            rule_id="R006", rule_name="Appointment compliance",
            violated=None, confidence=raw_conf,
            evidence_refs=["evidence.appointment_time", "evidence.terminal_records"],
            reasoning=(
                f"LLM confidence too low ({raw_conf:.2f}) to reach a reliable "
                "determination. " + assessment.reasoning
            ),
            citation=get_citation("R006").verbatim_text,
            missing_evidence=[
                _make_missing(
                    "evidence.appointment_time",
                    "Additional appointment evidence needed to reach a reliable conclusion.",
                    ["tms", "carrier_portal"],
                ),
            ],
        )

    return RuleResult(
        rule_id="R006", rule_name="Appointment compliance",
        violated=assessment.violated,
        confidence=_clamp_confidence(raw_conf),
        evidence_refs=["evidence.appointment_time", "invoice.free_time_start",
                       "invoice.free_time_end"],
        reasoning=assessment.reasoning,
        citation=get_citation("R006").verbatim_text,
    )


def evaluate_R006(case: DisputeCase) -> RuleResult:
    """Evaluate R006: appointment compliance under §541.6(e)(2).

    LLM-primary rule.  Evidence gate requires at least one appointment-time
    source.  The LLM determines whether the carrier's own performance (late
    gate opening, premature gate closure, dock congestion, scheduling errors)
    caused or contributed to the billed delay, invalidating the §541.6(e)(2)
    certification.

    Args:
        case: The ``DisputeCase`` under audit.

    Returns:
        ``violated=True``  — carrier caused or contributed to the delay.
        ``violated=False`` — shipper was responsible for missed appointment.
        ``violated=None``  — insufficient evidence or LLM confidence < 0.6.
    """
    rule_id = "R006"
    logger.info("Evaluating %s (Appointment compliance) for case %s", rule_id, case.case_id)

    if not _can_evaluate_R006(case):
        logger.info("R006 cannot evaluate: no appointment evidence")
        return _cannot_evaluate_R006(case)

    return _evaluate_R006_via_llm(case)


# ---------------------------------------------------------------------------
# R007 — Force majeure events  §541.6(e)(2) + 85 FR 29638
# ---------------------------------------------------------------------------

def _can_evaluate_R007(case: DisputeCase) -> bool:
    """True if force majeure evidence AND billed-period timestamps are present."""
    inv = case.invoice
    if not inv.free_time_start or not inv.free_time_end:
        return False  # can't test overlap without a billed period

    has_weather = bool(case.evidence.weather_events)
    has_closure = any(
        r.event_type == "terminal_closure"
        for r in case.evidence.terminal_records
    )
    return has_weather or has_closure


def _cannot_evaluate_R007(case: DisputeCase) -> RuleResult:
    """Build a cannot_evaluate RuleResult when R007 lacks force-majeure evidence."""
    inv = case.invoice
    missing: list[MissingEvidenceItem] = []

    if not inv.free_time_start or not inv.free_time_end:
        missing.append(_make_missing(
            "invoice.free_time_start / invoice.free_time_end",
            "Billed free-time window required to determine whether a force "
            "majeure event overlapped the charged period.",
            ["carrier_portal", "tms"],
        ))

    missing += [
        _make_missing(
            "evidence.weather_events",
            "Weather event descriptions needed to assess force majeure overlap.",
            ["external_weather_api"],
        ),
        _make_missing(
            "evidence.terminal_records[terminal_closure]",
            "Terminal closure records needed as alternative force majeure source.",
            ["terminal_portal", "carrier_portal"],
        ),
    ]

    return RuleResult(
        rule_id="R007", rule_name="Force majeure events",
        violated=None, confidence=0.0, evidence_refs=[],
        reasoning=(
            "Cannot evaluate: force majeure data (weather_events or terminal "
            "closure records) and/or billed free-time window timestamps are absent."
        ),
        citation=get_citation("R007").verbatim_text,
        missing_evidence=missing,
    )


def _evaluate_R007_via_llm(case: DisputeCase) -> RuleResult:
    """LLM applies the incentive-function test for force majeure events."""
    from agents.compliance_auditor.prompts import R007_FORCE_MAJEURE_PROMPT

    inv, evid = case.invoice, case.evidence

    closure_records = [
        r for r in evid.terminal_records
        if r.event_type == "terminal_closure"
    ]
    evidence_summary = (
        f"Billed detention/demurrage period: {inv.free_time_start} to "
        f"{inv.free_time_end} ({inv.hours_billed:.1f} hours billed).\n"
        f"Carrier: {inv.carrier_name}. Charge type: {inv.charge_type}.\n"
        f"Weather events reported: "
        + ("; ".join(evid.weather_events) or "none")
        + "\nTerminal closure records: "
        + (
            "; ".join(
                f"[{r.record_id}] closure at {r.event_timestamp}"
                + (f" — {r.notes}" if r.notes else "")
                for r in closure_records
            ) or "none"
        )
        + f"\nBilled party: {inv.billed_to_party}."
    )

    supporting_text = get_citation("R007").supporting_authorities[0].verbatim_text

    llm = get_llm_client(purpose="R007_force_majeure")
    chain = llm.with_structured_output(_LLMAssessment)
    assessment: _LLMAssessment = chain.invoke(
        R007_FORCE_MAJEURE_PROMPT.format_messages(
            citation=get_citation("R007").verbatim_text,
            supporting_authority=supporting_text,
            evidence_summary=evidence_summary,
        )
    )

    raw_conf = assessment.confidence
    if raw_conf < 0.6:
        logger.info(
            "R007 LLM confidence %.2f below 0.6 threshold — returning cannot_evaluate",
            raw_conf,
        )
        return RuleResult(
            rule_id="R007", rule_name="Force majeure events",
            violated=None, confidence=raw_conf,
            evidence_refs=["evidence.weather_events", "evidence.terminal_records",
                           "invoice.free_time_start", "invoice.free_time_end"],
            reasoning=(
                f"LLM confidence too low ({raw_conf:.2f}) to reach a reliable "
                "determination. " + assessment.reasoning
            ),
            citation=get_citation("R007").verbatim_text,
            missing_evidence=[
                _make_missing(
                    "evidence.weather_events",
                    "More specific event data needed for a reliable determination.",
                    ["external_weather_api"],
                ),
            ],
        )

    return RuleResult(
        rule_id="R007", rule_name="Force majeure events",
        violated=assessment.violated,
        confidence=_clamp_confidence(raw_conf),
        evidence_refs=["evidence.weather_events", "evidence.terminal_records",
                       "invoice.free_time_start", "invoice.free_time_end"],
        reasoning=assessment.reasoning,
        citation=get_citation("R007").verbatim_text,
    )


def evaluate_R007(case: DisputeCase) -> RuleResult:
    """Evaluate R007: force majeure events under §541.6(e)(2) + 85 FR 29638.

    LLM-primary rule.  Evidence gate requires force majeure event data (weather
    or terminal closure records) AND both free-time boundary timestamps.  The LLM
    applies the FMC incentive-function test from 85 FR 29638: if a named event
    prevented the receiver from retrieving or returning the container during the
    billed period, the charge cannot serve its incentive function and is
    unreasonable — therefore the §541.6(e)(2) certification is false.

    The supporting authority (85 FR 29638) is passed to the LLM in the prompt
    but does NOT appear in the ``citation`` field — that carries the primary
    §541.6(e)(2) text only, per the architectural contract.

    Args:
        case: The ``DisputeCase`` under audit.

    Returns:
        ``violated=True``  — force majeure event overlapped billed period and
                            the charge cannot serve its incentive function.
        ``violated=False`` — no qualifying force majeure overlap found.
        ``violated=None``  — insufficient evidence or LLM confidence < 0.6.
    """
    rule_id = "R007"
    logger.info("Evaluating %s (Force majeure events) for case %s", rule_id, case.case_id)

    if not _can_evaluate_R007(case):
        logger.info("R007 cannot evaluate: missing force majeure evidence or timestamps")
        return _cannot_evaluate_R007(case)

    return _evaluate_R007_via_llm(case)
