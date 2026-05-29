"""Output schema for DemandSmith's dispute letter.

DemandSmith is the Stage 3 letter drafter.  It consumes a ComplianceVerdict
(from ComplianceAuditor) and a RecoveryScore (from RecoveryOracle) and produces
a DemandLetter ready for transmission to the carrier.

Design discipline — what is and is not LLM-generated
-----------------------------------------------------
DemandLetter carries both template-derived fields and LLM-generated prose, but
the schema itself enforces only structural validity.  The discipline lives in
demand_smith.py and templates.py:

  Template-derived (never LLM):
    demand_amount_usd   — pulled from invoice.total_amount_usd
    response_deadline   — filing_date + 30 calendar days (§541.8(b))
    cited_rules         — R00X IDs of confirmed violations
    supporting_authorities_cited — canonical citation strings from citations registry
    word_count          — len(letter_text.split())
    filing_date         — parameter to draft_letter(), defaults to date.today()

  LLM-generated (prose inside letter_text):
    Opening narrative paragraph tying violations to case-specific evidence.
    Tone-appropriate closing language.
    Verbatim CFR text in letter_text comes from violation.citation and
    get_citation().verbatim_text — the LLM is not asked to produce it.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DemandLetter(BaseModel):
    """Fully assembled dispute letter produced by DemandSmith.

    Attributes:
        case_id: Case ID echoed from the DisputeCase, for downstream correlation.
        letter_text: Complete ready-to-send formal dispute letter, including
            letterhead, narrative, citation blocks, demand, and closing.
            Verbatim CFR text in this field is sourced deterministically from
            violation.citation and the citations registry — not LLM-generated.
        tone: Letter tone selected from RecoveryScore.recommended_action.
            auto_file → "firm"; human_review → "factual"; write_off → "factual"
            (with header_note populated).  "escalation" reserved for future use.
        cited_rules: R00X rule IDs whose violations are cited in the letter body,
            in severity order (high-confidence legally-decisive rules first).
        demand_amount_usd: Dollar amount disputed.  Pulled from
            invoice.total_amount_usd — never LLM-generated.
        filing_date: Date on which the dispute letter is being filed.
            Passed as a parameter to draft_letter(); defaults to date.today().
            Used to compute response_deadline and to assert §541.8(a) timeliness.
        response_deadline: Date by which the carrier must respond.
            Always filing_date + 30 calendar days, citing §541.8(b).
            Validated: must equal filing_date + timedelta(days=30).
        word_count: Word count of letter_text, computed as len(letter_text.split()).
            Validated to match the actual letter_text split count.
        supporting_authorities_cited: Canonical citation strings for any
            supporting (non-CFR) authorities quoted in the letter, e.g.
            "FMC Interpretive Rule, 85 FR 29638 (May 18, 2020), ...".
            Populated from Citation.supporting_authorities via get_citation().
            Empty list if no cited violation has supporting authorities.
        header_note: Prepended advisory note for write_off cases, e.g.
            "NOTE: RecoveryOracle assessed recovery probability below threshold.
            This draft is provided for human review only."
            None for firm and factual tones on normal cases.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(..., min_length=1, description="Case ID echoed from DisputeCase")
    letter_text: str = Field(
        ...,
        min_length=100,
        description="Complete ready-to-send dispute letter body",
    )
    tone: Literal["factual", "firm", "escalation"] = Field(
        ..., description="Letter tone derived from RecoveryScore.recommended_action"
    )
    cited_rules: list[str] = Field(
        ...,
        min_length=1,
        description="R00X rule IDs cited in the letter, in severity order",
    )
    demand_amount_usd: Annotated[float, Field(gt=0)] = Field(
        ...,
        description="Invoice amount being disputed; template-derived, never LLM-generated",
    )
    filing_date: date = Field(
        ...,
        description="Date the dispute is filed; used to compute response_deadline",
    )
    response_deadline: date = Field(
        ...,
        description="Carrier response deadline = filing_date + 30 calendar days (§541.8(b))",
    )
    word_count: int = Field(
        ...,
        gt=0,
        description="Word count of letter_text; must match len(letter_text.split())",
    )
    supporting_authorities_cited: list[str] = Field(
        default_factory=list,
        description=(
            "Canonical citation strings for supporting authorities quoted in the letter. "
            "Populated from Citation.supporting_authorities via citations registry."
        ),
    )
    header_note: str | None = Field(
        default=None,
        description=(
            "Advisory note prepended to write_off drafts. "
            "None for firm and factual tones on normal cases."
        ),
    )

    @model_validator(mode="after")
    def word_count_matches_letter(self) -> "DemandLetter":
        """Verify word_count equals len(letter_text.split())."""
        actual = len(self.letter_text.split())
        if self.word_count != actual:
            raise ValueError(
                f"word_count={self.word_count} does not match "
                f"actual word count of letter_text ({actual} words)"
            )
        return self

    @model_validator(mode="after")
    def deadline_equals_filing_plus_30(self) -> "DemandLetter":
        """Verify response_deadline == filing_date + 30 calendar days."""
        expected = self.filing_date + timedelta(days=30)
        if self.response_deadline != expected:
            raise ValueError(
                f"response_deadline={self.response_deadline} must equal "
                f"filing_date ({self.filing_date}) + 30 days = {expected}"
            )
        return self

    @model_validator(mode="after")
    def cited_rules_format(self) -> "DemandLetter":
        """Verify every cited_rule matches the R\\d{{3}} pattern."""
        bad = [r for r in self.cited_rules if not re.match(r"^R\d{3}$", r)]
        if bad:
            raise ValueError(f"cited_rules contains invalid rule ID(s): {bad}")
        return self

    @model_validator(mode="after")
    def header_note_only_on_low_odds(self) -> "DemandLetter":
        """Verify header_note is None when tone is firm or escalation."""
        if self.header_note is not None and self.tone in ("firm", "escalation"):
            raise ValueError(
                f"header_note must be None when tone='{self.tone}'; "
                "header_note is reserved for write_off/factual cases with low recovery odds"
            )
        return self
