"""Deterministic letter scaffolding for DemandSmith.

All functions in this module are pure Python — no LLM calls.  They produce the
structural, factual, and legal portions of the dispute letter that must never be
LLM-generated: the header block, citation paragraphs, deadline framing, demand
statement, and closing.

Template-vs-LLM boundary
-------------------------
This module owns everything outside the narrative.  The LLM (in demand_smith.py)
contributes one prose paragraph that ties violations to case-specific evidence.
Everything else is assembled from the inputs deterministically.

Severity ordering
-----------------
Violations are sorted by severity before any template function is called.
sort_violations_by_severity() implements the ordering; the sorted list is used
for both the LLM narrative prompt and the per-violation citation blocks.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from agents.compliance_auditor.citations import get_citation
from data.schemas.case import DisputeCase
from data.schemas.score import RecoveryScore
from data.schemas.verdict import ComplianceVerdict, Violation

# ---------------------------------------------------------------------------
# Severity ordering
# ---------------------------------------------------------------------------

# Rules that are legally decisive or carry the highest FMC weight come first.
# Within each tier, higher confidence ranks earlier (handled dynamically).
_TIER_ONE: frozenset[str] = frozenset({"R001", "R007"})   # dispositive on their own
_TIER_TWO: frozenset[str] = frozenset({"R002", "R004"})   # content/timing failures
_TIER_THREE: frozenset[str] = frozenset({"R005", "R006", "R009", "R010", "R011"})  # calc/procedure
_TIER_FOUR: frozenset[str] = frozenset({"R003", "R008", "R012"})  # disclosure / party rules


def _severity_key(v: Violation) -> tuple[int, float]:
    rid = v.rule_id
    if rid in _TIER_ONE:
        tier = 0
    elif rid in _TIER_TWO:
        tier = 1
    elif rid in _TIER_THREE:
        tier = 2
    else:
        tier = 3
    return (tier, -v.confidence)   # lower tier index + higher confidence = earlier


def sort_violations_by_severity(violations: list[Violation]) -> list[Violation]:
    """Return violations sorted severity-first (tier ascending, confidence descending)."""
    return sorted(violations, key=_severity_key)


# ---------------------------------------------------------------------------
# Tone selection
# ---------------------------------------------------------------------------


def select_tone(score: RecoveryScore) -> Literal["factual", "firm", "escalation"]:
    """Map RecoveryScore.recommended_action to letter tone.

    auto_file → "firm"; human_review → "factual"; write_off → "factual"
    (caller will populate header_note for write_off cases).
    """
    if score.recommended_action == "auto_file":
        return "firm"
    return "factual"


# ---------------------------------------------------------------------------
# Deadline computation
# ---------------------------------------------------------------------------


def compute_response_deadline(filing_date: date) -> date:
    """Return filing_date + 30 calendar days.

    This is the carrier's response window asserted in the letter under §541.8(b).
    §541.8(b) requires the billing party to attempt resolution within 30 calendar
    days of receiving a mitigation/refund/waiver request.  The demand letter
    constitutes that request; the deadline gives the carrier its statutory window.

    Note: §541.8(a) is the billed party's 30-day window to FILE the dispute
    (running from invoice_date).  That is a different clock; the §541.8(a)
    timeliness framing appears in the letter body, not in this deadline.
    """
    return filing_date + timedelta(days=30)


# ---------------------------------------------------------------------------
# Header block
# ---------------------------------------------------------------------------


def build_letterhead(case: DisputeCase, filing_date: date, response_deadline: date) -> str:
    """Produce the letterhead and RE: block.

    Includes: date, carrier name, invoice number, BOL/container, and the RE: line.
    """
    inv = case.invoice
    ref_lines: list[str] = []
    if inv.bol_number:
        ref_lines.append(f"BOL Number:       {inv.bol_number}")
    if inv.container_number:
        ref_lines.append(f"Container Number: {inv.container_number}")

    lines = [
        filing_date.strftime("%B %d, %Y"),
        "",
        inv.carrier_name,
        "Demurrage & Detention Disputes Department",
        "",
        f"RE: FORMAL DISPUTE — Invoice No. {inv.invoice_number}",
        *ref_lines,
        f"Invoice Amount: ${inv.total_amount_usd:,.2f}",
        f"Response Deadline: {response_deadline.strftime('%B %d, %Y')}"
        f" (30 calendar days per 46 CFR § 541.8(b))",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Salutation
# ---------------------------------------------------------------------------


def build_salutation() -> str:
    """Return the opening salutation."""
    return "To Whom It May Concern:\n"


# ---------------------------------------------------------------------------
# Timeliness framing sentence (§541.8(a) + §541.8(b))
# ---------------------------------------------------------------------------


def build_timeliness_paragraph(
    case: DisputeCase,
    filing_date: date,
    response_deadline: date,
) -> str:
    """Return the §541.8(a)/(b) dual-framing paragraph.

    States that the dispute is timely filed within the billed party's §541.8(a)
    window AND that the carrier must respond under §541.8(b) by the deadline.
    Computed entirely from invoice_date and filing_date — no LLM involvement.
    """
    inv = case.invoice
    days_elapsed = (filing_date - inv.invoice_date).days
    return (
        f"This dispute is submitted by {inv.billed_to_party} pursuant to 46 CFR § 541.8(a), "
        f"which provides the billed party at least thirty (30) calendar days from the invoice "
        f"issuance date to request mitigation, refund, or waiver. Invoice No. {inv.invoice_number} "
        f"is dated {inv.invoice_date.strftime('%B %d, %Y')}; this dispute is filed on "
        f"{filing_date.strftime('%B %d, %Y')} — {days_elapsed} calendar day(s) thereafter "
        f"— and is therefore timely. "
        f"Pursuant to 46 CFR § 541.8(b), {inv.carrier_name} must attempt to resolve this "
        f"request within thirty (30) calendar days of receipt — no later than "
        f"{response_deadline.strftime('%B %d, %Y')}."
    )


# ---------------------------------------------------------------------------
# Per-violation citation block
# ---------------------------------------------------------------------------


def build_citation_block(violation: Violation) -> str:
    """Produce the two-paragraph citation block for one confirmed violation.

    Paragraph 1: primary authority — verbatim text from violation.citation
    (already on the Violation model, sourced from get_citation().verbatim_text
    at ComplianceAuditor build time).

    Paragraph 2: supporting authority — from the citations registry.
    Omitted if the rule has no supporting_authorities.

    The LLM is not involved in this function.  All text is sourced from
    violation.citation (for primary) and get_citation().supporting_authorities
    (for supporting tier).
    """
    citation_obj = get_citation(violation.rule_id)
    rule_label = f"{violation.rule_id} — {violation.rule_name}"

    primary_block = (
        f"  Rule {rule_label}\n"
        f"  Regulatory Authority: {citation_obj.regulation_part}\n\n"
        f"  \"{violation.citation}\"\n"
        f"  (Confidence: {violation.confidence:.0%})"
    )

    if not citation_obj.supporting_authorities:
        return primary_block

    sa = citation_obj.supporting_authorities[0]
    supporting_block = (
        f"\n\n  This determination is reinforced by {sa.citation}:\n\n"
        f"  \"{sa.verbatim_text}\""
    )
    return primary_block + supporting_block


# ---------------------------------------------------------------------------
# Demand statement
# ---------------------------------------------------------------------------


def build_demand_statement(case: DisputeCase) -> str:
    """Return the formal demand paragraph.

    Dollar amount is pulled from invoice.total_amount_usd — never LLM-generated.
    """
    inv = case.invoice
    return (
        f"Based on the violations identified above, {inv.billed_to_party} hereby formally "
        f"demands that {inv.carrier_name} immediately cancel Invoice No. {inv.invoice_number} "
        f"in its entirety and issue a full credit or refund of ${inv.total_amount_usd:,.2f}. "
        f"In the alternative, {inv.billed_to_party} requests that {inv.carrier_name} provide "
        f"a written explanation of its basis for maintaining the charge, with specific "
        f"reference to each regulatory requirement identified in this dispute."
    )


# ---------------------------------------------------------------------------
# Closing
# ---------------------------------------------------------------------------


_CLOSING_FIRM = (
    "Failure to respond by the deadline stated above will be construed as "
    "{carrier_name}'s concession that the charge is not legally supportable and "
    "may result in referral of this matter to the Federal Maritime Commission for "
    "formal complaint proceedings under 46 U.S.C. § 41302."
)

_CLOSING_FACTUAL = (
    "{carrier_name} is respectfully requested to review the foregoing and provide "
    "a written response by the deadline stated above. {billed_party} reserves all "
    "rights and remedies available under the Shipping Act of 1984 and applicable "
    "FMC regulations."
)

_CLOSING_ESCALATION = (
    "This dispute has been escalated for expedited review. Absent a satisfactory "
    "response from {carrier_name} by the stated deadline, {billed_party} will "
    "immediately file a formal complaint with the Federal Maritime Commission "
    "pursuant to 46 U.S.C. § 41302 and will seek all available remedies, "
    "including civil penalties."
)


def build_closing(
    tone: Literal["factual", "firm", "escalation"],
    case: DisputeCase,
) -> str:
    """Return the closing paragraph and signature block."""
    inv = case.invoice
    template = {
        "firm": _CLOSING_FIRM,
        "factual": _CLOSING_FACTUAL,
        "escalation": _CLOSING_ESCALATION,
    }[tone]

    closing_text = template.format(
        carrier_name=inv.carrier_name,
        billed_party=inv.billed_to_party,
    )

    return (
        f"{closing_text}\n\n"
        f"Respectfully submitted,\n\n"
        f"{inv.billed_to_party}\n"
        f"[Authorized Representative]\n"
        f"[Title]\n"
        f"[Contact Information]"
    )


# ---------------------------------------------------------------------------
# Write_off header note
# ---------------------------------------------------------------------------


def build_write_off_header_note(score: RecoveryScore) -> str:
    """Return the advisory header note for write_off cases."""
    return (
        f"[INTERNAL NOTE — FOR HUMAN REVIEW ONLY: RecoveryOracle assessed this case "
        f"with a recovery probability of {score.recovery_probability:.0%} and a "
        f"recommended action of 'write_off'. This demand letter draft is provided "
        f"for human review before any filing decision is made. Do not transmit "
        f"without explicit authorization from the disputes team.]\n\n"
    )
