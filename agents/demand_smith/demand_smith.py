"""DemandSmith — LangChain agent for dispute letter drafting.

DemandSmith is the Stage 3 letter drafter in the DisputeOps case lifecycle.
It runs after ComplianceAuditor and RecoveryOracle, consuming both their outputs
plus the original DisputeCase, and produces a complete DemandLetter.

Framework: LangChain 0.3.x
  ChatPromptTemplate + shared get_llm_client() factory → plain string output
  for the narrative paragraph; all structural letter assembly is deterministic.

Template-vs-LLM discipline
---------------------------
The LLM generates ONE prose paragraph — the opening argument that ties violations
to evidence.  Everything else is deterministic Python:
  - Letterhead, salutation, deadline, demand, closing → templates.py
  - CFR citation text → violation.citation + citations registry
  - Dollar amounts, dates, cited rule IDs → pulled directly from inputs

Severity ordering
-----------------
Violations are sorted by sort_violations_by_severity() before the LLM call.
The sorted list drives both the narrative prompt and the citation block order,
so the letter reads in the same logical sequence as the prompt.

Model string format
-------------------
DEMAND_SMITH_MODEL env var must contain a bare model name (e.g. "gpt-4o-mini")
— no "openai:" prefix.  The shared get_llm_client() wraps ChatOpenAI, which
expects the bare name.  This differs from RecoveryOracle, which uses Pydantic
AI's native model layer and requires the "openai:" prefix.
"""

from __future__ import annotations

import logging
import os
from datetime import date

from agents.compliance_auditor.citations import get_citation
from agents.demand_smith.prompts import NARRATIVE_PROMPT, TONE_INSTRUCTIONS
from agents.demand_smith.templates import (
    build_citation_block,
    build_closing,
    build_demand_statement,
    build_letterhead,
    build_salutation,
    build_timeliness_paragraph,
    build_write_off_header_note,
    compute_response_deadline,
    select_tone,
    sort_violations_by_severity,
)
from agents.shared.llm_client import get_llm_client
from data.schemas.case import DisputeCase
from data.schemas.letter import DemandLetter
from data.schemas.score import RecoveryScore
from data.schemas.verdict import ComplianceVerdict, Violation

logger = logging.getLogger(__name__)

_MODEL_NAME: str = os.environ.get("DEMAND_SMITH_MODEL", "gpt-4o-mini")


# ---------------------------------------------------------------------------
# Narrative generation
# ---------------------------------------------------------------------------


def _build_violation_summaries(violations: list[Violation]) -> str:
    """Format the violation list for the narrative prompt."""
    lines = []
    for i, v in enumerate(violations, 1):
        lines.append(
            f"{i}. {v.rule_id} ({v.rule_name}) — confidence {v.confidence:.0%}\n"
            f"   Finding: {v.reasoning}"
        )
    return "\n\n".join(lines)


def _build_weather_context(case: DisputeCase) -> str:
    """Return a formatted weather/force-majeure context line, or empty string."""
    events = case.evidence.weather_events
    if not events:
        return ""
    return "Force majeure events on record:\n" + "\n".join(f"  - {e}" for e in events)


def _generate_narrative(
    verdict: ComplianceVerdict,
    score: RecoveryScore,
    case: DisputeCase,
    violations: list[Violation],
    tone: str,
) -> str:
    """Invoke the LLM to generate the opening narrative paragraph.

    Returns the prose string directly.  Temperature 0 for determinism.
    """
    llm = get_llm_client(purpose="demand_smith_narrative", model=_MODEL_NAME)
    chain = NARRATIVE_PROMPT | llm

    key_factors_text = "\n".join(f"  - {f}" for f in score.key_factors)

    result = chain.invoke({
        "tone_instruction": TONE_INSTRUCTIONS[tone],
        "carrier_name": case.invoice.carrier_name,
        "billed_to_party": case.invoice.billed_to_party,
        "invoice_number": case.invoice.invoice_number,
        "charge_type": case.invoice.charge_type,
        "invoice_date": case.invoice.invoice_date.strftime("%B %d, %Y"),
        "charge_incurred_date": case.invoice.charge_incurred_date.strftime("%B %d, %Y"),
        "hours_billed": case.invoice.hours_billed,
        "demand_amount_usd": case.invoice.total_amount_usd,
        "weather_context": _build_weather_context(case),
        "violation_summaries": _build_violation_summaries(violations),
        "overall_strength": verdict.overall_strength,
        "recovery_probability": score.recovery_probability,
        "key_factors": key_factors_text,
    })

    return result.content.strip()


# ---------------------------------------------------------------------------
# Letter assembly
# ---------------------------------------------------------------------------


def _collect_supporting_authority_citations(violations: list[Violation]) -> list[str]:
    """Return canonical citation strings for supporting authorities referenced."""
    seen: set[str] = set()
    result: list[str] = []
    for v in violations:
        citation_obj = get_citation(v.rule_id)
        for sa in citation_obj.supporting_authorities:
            if sa.citation not in seen:
                seen.add(sa.citation)
                result.append(sa.citation)
    return result


def _assemble_letter(
    narrative: str,
    violations: list[Violation],
    verdict: ComplianceVerdict,
    score: RecoveryScore,
    case: DisputeCase,
    filing_date: date,
    response_deadline: date,
    tone: str,
    header_note: str | None,
) -> str:
    """Assemble the full letter_text from template blocks and LLM narrative."""
    sections: list[str] = []

    if header_note:
        sections.append(header_note)

    sections.append(build_letterhead(case, filing_date, response_deadline))
    sections.append(build_salutation())
    sections.append(build_timeliness_paragraph(case, filing_date, response_deadline))
    sections.append("")
    sections.append(narrative)
    sections.append("")
    sections.append("The specific regulatory violations are as follows:")
    sections.append("")

    for v in violations:
        sections.append(build_citation_block(v))
        sections.append("")

    sections.append(build_demand_statement(case))
    sections.append("")
    sections.append(build_closing(tone, case))

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def draft_letter(
    verdict: ComplianceVerdict,
    score: RecoveryScore,
    case: DisputeCase,
    filing_date: date | None = None,
) -> DemandLetter:
    """Draft a formal FMC Part 541 dispute letter for a detention/demurrage case.

    Orchestrates templates.py (deterministic scaffolding) and the LangChain
    narrative agent (one prose paragraph) to produce a fully assembled DemandLetter.

    Args:
        verdict: ComplianceVerdict from ComplianceAuditor.  Only
            verdict.violations drive the letter; cannot_evaluate items are excluded.
        score: RecoveryScore from RecoveryOracle.  Drives tone selection and
            provides key_factors for the narrative prompt.
        case: The original DisputeCase.  Provides invoice facts used in the
            letterhead, timeliness statement, and demand paragraph.
        filing_date: Date the dispute is filed.  Defaults to date.today().
            Used to compute the §541.8(b) response deadline (filing_date + 30).

    Returns:
        A fully validated DemandLetter with all fields populated.

    Raises:
        ValueError: If verdict.violations is empty (no confirmed violations to cite).
        pydantic.ValidationError: If the assembled letter fails DemandLetter validation.
    """
    if not verdict.violations:
        raise ValueError(
            f"case_id={case.case_id}: verdict has no confirmed violations; "
            "DemandSmith requires at least one violation to draft a letter. "
            "RouteMatrix should not route no-merit or write_off cases to DemandSmith."
        )

    resolved_filing_date = filing_date or date.today()
    response_deadline = compute_response_deadline(resolved_filing_date)
    tone = select_tone(score)
    violations = sort_violations_by_severity(list(verdict.violations))

    header_note: str | None = None
    if score.recommended_action == "write_off":
        header_note = build_write_off_header_note(score)

    logger.info(
        "DemandSmith: drafting letter case_id=%s  tone=%s  violations=%d  "
        "filing_date=%s  deadline=%s",
        case.case_id, tone, len(violations),
        resolved_filing_date, response_deadline,
    )

    narrative = _generate_narrative(verdict, score, case, violations, tone)

    logger.info(
        "DemandSmith: narrative generated case_id=%s  words=%d",
        case.case_id, len(narrative.split()),
    )

    letter_text = _assemble_letter(
        narrative=narrative,
        violations=violations,
        verdict=verdict,
        score=score,
        case=case,
        filing_date=resolved_filing_date,
        response_deadline=response_deadline,
        tone=tone,
        header_note=header_note,
    )

    cited_rules = [v.rule_id for v in violations]
    supporting_authorities = _collect_supporting_authority_citations(violations)

    letter = DemandLetter(
        case_id=case.case_id,
        letter_text=letter_text,
        tone=tone,
        cited_rules=cited_rules,
        demand_amount_usd=case.invoice.total_amount_usd,
        filing_date=resolved_filing_date,
        response_deadline=response_deadline,
        word_count=len(letter_text.split()),
        supporting_authorities_cited=supporting_authorities,
        header_note=header_note.strip() if header_note else None,
    )

    logger.info(
        "DemandSmith: letter complete case_id=%s  word_count=%d  "
        "cited_rules=%s  supporting_authorities=%d",
        case.case_id, letter.word_count,
        letter.cited_rules, len(letter.supporting_authorities_cited),
    )

    return letter
