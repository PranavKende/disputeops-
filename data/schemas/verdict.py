"""Output schemas for ComplianceAuditor verdicts.

These models are returned by the LangGraph agent and consumed downstream by
RecoveryOracle (probability scorer) and DemandSmith (letter drafter).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RuleResult(BaseModel):
    """Raw result returned by a single rule evaluation function in rules.py.

    Each of the 11 rule functions produces one RuleResult.  The LangGraph
    synthesis node then aggregates these into a ComplianceVerdict.

    Attributes:
        rule_id: Short identifier matching the R001–R011 naming convention.
        rule_name: Human-readable name of the rule (used in summaries).
        violated: ``True`` = rule is violated; ``False`` = rule passes;
            ``None`` = cannot evaluate due to missing evidence.
        confidence: A float in [0.0, 1.0] expressing how confident the
            evaluator is in the ``violated`` determination.  For deterministic
            math rules this will typically be 1.0 or 0.0.  For LLM-evaluated
            rules it reflects model-reported confidence.
        evidence_refs: List of evidence field names or terminal record IDs
            that were consulted when evaluating this rule, e.g.
            ``["invoice.invoice_date", "invoice.charge_incurred_date"]``.
        reasoning: A one-to-three sentence plain-English explanation of why
            the rule passed, failed, or could not be evaluated.
        citation: Verbatim FMC Part 541 regulatory text relevant to this rule.
        missing_evidence: When ``violated is None``, a non-empty list of
            structured evidence gaps explaining what is missing and why each
            item is required.  ``None`` when ``violated`` is ``True`` or
            ``False``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str = Field(..., pattern=r"^R\d{3}$", description="R001–R011 rule identifier")
    rule_name: str = Field(..., description="Human-readable rule name")
    violated: bool | None = Field(
        ...,
        description=(
            "True=violated, False=passes, None=cannot evaluate due to missing evidence"
        ),
    )
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = Field(
        ..., description="Evaluator confidence in the violated determination [0–1]"
    )
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="Evidence fields / terminal record IDs consulted",
    )
    reasoning: str = Field(
        ..., description="Plain-English explanation of the evaluation result"
    )
    citation: str = Field(
        ..., description="Verbatim FMC Part 541 text relevant to this rule"
    )
    missing_evidence: list[MissingEvidenceItem] | None = Field(
        default=None,
        description="Structured evidence gaps when violated is None; None otherwise",
    )


class Violation(BaseModel):
    """A confirmed or probable FMC Part 541 violation found in a case.

    Violations are extracted from RuleResults where ``violated is True``.
    Cannot-evaluate results are stored separately in ComplianceVerdict.

    Attributes:
        rule_id: R001–R011 identifier of the violated rule.
        rule_name: Human-readable rule name.
        confidence: Confidence score from the rule evaluator.
        evidence_refs: Evidence items that establish the violation.
        reasoning: Plain-English explanation of the violation.
        citation: Verbatim FMC Part 541 text that the carrier violated.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str = Field(..., pattern=r"^R\d{3}$")
    rule_name: str
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    evidence_refs: list[str] = Field(default_factory=list)
    reasoning: str
    citation: str


class MissingEvidenceItem(BaseModel):
    """A single piece of missing evidence that prevented a rule from evaluating.

    Using a structured list (rather than a free-text string) enables two
    downstream features without requiring schema changes: Stage 2 retry routing
    (RecoveryOracle can suggest which source could close the gap) and
    demo-quality reasoning strings in DemandSmith.

    Attributes:
        field_path: Dotted path to the missing field on the case model, e.g.
            ``"evidence.gate_in_timestamp"`` or
            ``"invoice.free_time_start"``.
        description: Human-readable explanation of what is missing and exactly
            why the rule needs it to reach a determination.
        can_be_fetched_from: Optional hint listing the source systems that
            could supply this field — used by RecoveryOracle to suggest
            evidence-retry actions.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_path: str = Field(
        ...,
        description="Dotted path to the missing field, e.g. 'evidence.gate_in_timestamp'",
    )
    description: str = Field(
        ...,
        description=(
            "Human-readable explanation of what is missing and why the rule needs it"
        ),
    )
    can_be_fetched_from: (
        list[Literal["tms", "carrier_portal", "driver_log", "terminal_portal",
                     "email", "manual", "external_weather_api"]]
        | None
    ) = Field(
        default=None,
        description="Source systems that could supply this evidence field",
    )


class CannotEvaluate(BaseModel):
    """A rule that could not be evaluated due to missing evidence.

    Kept as a separate model from ``Violation`` because the downstream
    consumers diverge: DemandSmith iterates only violations; RecoveryOracle
    reads both violations and cannot_evaluate with different weights.  Forcing
    them into one model with nullable fields would lose semantic clarity.

    Attributes:
        rule_id: R001–R011 identifier of the unevaluated rule.
        rule_name: Human-readable rule name.
        missing_evidence: Non-empty list of structured evidence gaps.  Each
            item names the missing field, explains why it is needed, and
            optionally hints at retrieval sources.
        reasoning: Plain-English explanation of why the evidence gaps prevent
            evaluation of this specific rule.
        citation: FMC Part 541 text for the rule that was skipped.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str = Field(..., pattern=r"^R\d{3}$")
    rule_name: str
    missing_evidence: list[MissingEvidenceItem] = Field(
        ...,
        min_length=1,
        description="Non-empty list of structured evidence gaps",
    )
    reasoning: str = Field(
        ...,
        description="Plain-English explanation of why evaluation was not possible",
    )
    citation: str


class ComplianceVerdict(BaseModel):
    """Full compliance audit output produced by ComplianceAuditor.

    This is the root output model consumed by RecoveryOracle and DemandSmith.

    Attributes:
        case_id: Echoed from the input DisputeCase for correlation.
        total_rules_evaluated: Number of rules that produced a definitive
            True/False result (i.e. ``len(violations) + len(clean_results)``).
        violations: Rules where a violation was found (violated=True).
        cannot_evaluate: Rules skipped due to missing evidence.
        clean_results: Rule IDs where no violation was found (violated=False).
        overall_strength: Assessment of the overall dispute strength:
            - ``"strong"``     ≥ 2 high-confidence violations (conf ≥ 0.8)
            - ``"moderate"``   1 high-confidence violation, or ≥ 2 medium
            - ``"weak"``       only low-confidence violations (conf < 0.6)
            - ``"no_merit"``   zero violations found
        summary: Demand-letter-ready plain-English narrative (2–4 sentences)
            describing the violations found and their regulatory basis.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(..., description="Case ID echoed from DisputeCase")
    total_rules_evaluated: Annotated[int, Field(ge=0)] = Field(
        ..., description="Count of rules with a definitive True/False result"
    )
    violations: list[Violation] = Field(
        default_factory=list,
        description="Rules where a violation was detected",
    )
    cannot_evaluate: list[CannotEvaluate] = Field(
        default_factory=list,
        description="Rules skipped due to missing evidence",
    )
    clean_results: list[str] = Field(
        default_factory=list,
        description="Rule IDs that passed (no violation found)",
    )
    overall_strength: Literal["strong", "moderate", "weak", "no_merit"] = Field(
        ..., description="Assessed strength of the shipper's dispute"
    )
    summary: str = Field(
        ..., description="Demand-letter-ready plain-English summary of findings"
    )

    @model_validator(mode="after")
    def totals_are_consistent(self) -> "ComplianceVerdict":
        """Verify total_rules_evaluated equals violations + clean_results."""
        actual = len(self.violations) + len(self.clean_results)
        if self.total_rules_evaluated != actual:
            raise ValueError(
                f"total_rules_evaluated={self.total_rules_evaluated} does not match "
                f"len(violations)={len(self.violations)} + "
                f"len(clean_results)={len(self.clean_results)} = {actual}"
            )
        return self
