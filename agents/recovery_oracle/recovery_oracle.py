"""RecoveryOracle — Pydantic AI agent for recovery probability scoring.

RecoveryOracle is the Stage 3 recovery scorer in the DisputeOps case lifecycle.
It runs immediately after ComplianceAuditor and produces a RecoveryScore consumed
by RouteMatrix (DMN routing), DemandSmith (letter drafting), and RecoveryPulse
(telemetry dashboard).

Framework: Pydantic AI 1.x
  - Agent(model=..., output_type=_RecoveryScoreFromLLM, deps_type=ScoringContext)
  - @agent.system_prompt decorator injects structured case data at call time
  - run_sync("...", deps=ScoringContext(...)) for the synchronous call path
  - result.output returns the validated _RecoveryScoreFromLLM instance

Design pattern — expected_recovery_usd
----------------------------------------
The LLM is NOT asked to compute expected_recovery_usd.  LLMs hallucinate on
arithmetic.  Instead, the internal _RecoveryScoreFromLLM model (the LLM's
output_type) omits that field.  The public score_recovery() wrapper derives it
in Python after the LLM call:
    expected_recovery_usd = round(probability × invoice.total_amount_usd, 2)
The final RecoveryScore is then constructed from the LLM's output plus this
Python-derived field.  See data/schemas/score.py for the full schema design note.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai import Agent, RunContext

from agents.recovery_oracle.prompts import (
    NEGATIVE_FACTORS_SECTION,
    OUTPUT_FORMAT_SECTION,
    POSITIVE_FACTORS_SECTION,
    RECOVERY_ORACLE_USER_PROMPT,
    ROLE_SECTION,
    SCORING_RULES_SECTION,
)
from data.schemas.case import DisputeCase
from data.schemas.score import RecoveryScore
from data.schemas.verdict import ComplianceVerdict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ScoringContext — dependency injection carrier
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ScoringContext:
    """Carries ComplianceVerdict and DisputeCase into the Pydantic AI agent.

    Passed as deps= at call time.  The @agent.system_prompt builder reads
    ctx.deps.verdict and ctx.deps.case to inject structured data into the
    prompt without polluting the user prompt string.

    Attributes:
        verdict: ComplianceAuditor output for the case being scored.
        case: The original DisputeCase (used for invoice dollar context
            and carrier identity).
    """

    verdict: ComplianceVerdict
    case: DisputeCase


# ---------------------------------------------------------------------------
# _RecoveryScoreFromLLM — internal output_type for the Pydantic AI agent
#
# Same shape as RecoveryScore but WITHOUT expected_recovery_usd.
# The LLM is responsible for probability estimation (a reasoning task);
# Python is responsible for the probability × amount multiplication (a math task).
# score_recovery() builds the final RecoveryScore from this model + arithmetic.
# ---------------------------------------------------------------------------


class _RecoveryScoreFromLLM(BaseModel):
    """Internal output model for the Pydantic AI agent.

    Omits expected_recovery_usd — that field is computed in Python post-call.
    Do not use this model outside recovery_oracle.py.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    recovery_probability: Annotated[float, Field(ge=0.0, le=1.0)]
    recommended_action: Literal["auto_file", "human_review", "write_off"]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    reasoning: str
    key_factors: list[str] = Field(min_length=1, max_length=5)

    @model_validator(mode="before")
    @classmethod
    def clip_confidence(cls, data: object) -> object:
        """Clip confidence into [0.6, 0.95] before field validation.

        Mirrors the same validator on the public RecoveryScore so that
        out-of-range LLM responses are corrected at parse time rather
        than triggering a validation error and a Pydantic AI retry.
        """
        if isinstance(data, dict) and "confidence" in data:
            raw = data["confidence"]
            if isinstance(raw, (int, float)):
                data = {**data, "confidence": float(max(0.6, min(0.95, raw)))}
        return data

    @model_validator(mode="after")
    def low_confidence_forces_human_review(self) -> "_RecoveryScoreFromLLM":
        """Force human_review when confidence is at or near floor."""
        if self.confidence < 0.66 and self.recommended_action != "human_review":
            raise ValueError(
                f"confidence={self.confidence:.3f} < 0.66; "
                f"recommended_action must be 'human_review', "
                f"got '{self.recommended_action}'."
            )
        return self


# ---------------------------------------------------------------------------
# Agent initialization
# ---------------------------------------------------------------------------

_MODEL_NAME: str = os.environ.get("RECOVERY_ORACLE_MODEL", "openai:gpt-4o-mini")

_agent: Agent[ScoringContext, _RecoveryScoreFromLLM] = Agent(
    model=_MODEL_NAME,
    output_type=_RecoveryScoreFromLLM,
    deps_type=ScoringContext,
    model_settings={"temperature": 0},
    defer_model_check=True,  # skip credential check at import time; checked at first call
)


@_agent.system_prompt
def _build_system_prompt(ctx: RunContext[ScoringContext]) -> str:  # type: ignore[misc]
    """Build the system prompt from static sections plus live case data.

    The static sections (role, scoring rules, factor guides, output format) are
    defined as string constants in prompts.py and assembled here.  The live data
    (verdict JSON, invoice amount, carrier name, case ID) is injected from the
    deps context so the agent has full case context without a megaprompt that
    mixes logic and data.

    Args:
        ctx: Pydantic AI RunContext carrying the ScoringContext deps.

    Returns:
        Full system prompt string for this scoring call.
    """
    verdict = ctx.deps.verdict
    case = ctx.deps.case

    # Serialize only the fields the agent needs to reason about.
    # Full verdict JSON gives all rule findings; invoice provides dollar context.
    verdict_summary = (
        f"overall_strength: {verdict.overall_strength}\n"
        f"total_rules_evaluated: {verdict.total_rules_evaluated}\n"
        f"violation_count: {len(verdict.violations)}\n"
        f"cannot_evaluate_count: {len(verdict.cannot_evaluate)}\n"
        f"clean_result_count: {len(verdict.clean_results)}\n"
        f"summary: {verdict.summary}\n\n"
        "violations:\n"
        + "\n".join(
            f"  - {v.rule_id} ({v.rule_name}): conf={v.confidence:.2f}  {v.reasoning[:120]}"
            for v in verdict.violations
        )
        + ("\n\ncannot_evaluate:\n" if verdict.cannot_evaluate else "")
        + "\n".join(
            f"  - {c.rule_id} ({c.rule_name}): {c.reasoning[:80]}"
            for c in verdict.cannot_evaluate
        )
    )

    invoice_context = (
        f"invoice_amount_usd: {case.invoice.total_amount_usd:.2f}\n"
        f"carrier_name: {case.invoice.carrier_name}\n"
        f"charge_type: {case.invoice.charge_type}\n"
        f"case_id: {case.case_id}"
    )

    return "\n\n".join(
        [
            ROLE_SECTION,
            SCORING_RULES_SECTION,
            POSITIVE_FACTORS_SECTION,
            NEGATIVE_FACTORS_SECTION,
            OUTPUT_FORMAT_SECTION,
            "## Case to score\n\n### ComplianceAuditor verdict\n\n" + verdict_summary,
            "### Invoice context\n\n" + invoice_context,
        ]
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def score_recovery(verdict: ComplianceVerdict, case: DisputeCase) -> RecoveryScore:
    """Score the recovery probability for a detention/demurrage dispute case.

    Runs the RecoveryOracle Pydantic AI agent synchronously against the given
    ComplianceAuditor verdict and DisputeCase, then derives expected_recovery_usd
    in Python and returns the final RecoveryScore.

    Args:
        verdict: The ComplianceVerdict produced by ComplianceAuditor for this case.
        case: The original DisputeCase (provides invoice amount for arithmetic).

    Returns:
        A fully-validated RecoveryScore with all fields populated, including the
        Python-derived expected_recovery_usd.

    Raises:
        pydantic_ai.UnexpectedModelBehavior: If the LLM response cannot be parsed
            into _RecoveryScoreFromLLM after all retries are exhausted.
        pydantic.ValidationError: If the assembled RecoveryScore fails its own
            validators (should not happen in practice — mirrored validators on
            _RecoveryScoreFromLLM prevent this).

    Example:
        >>> score = score_recovery(verdict, case)
        >>> score.recommended_action
        'auto_file'
        >>> score.expected_recovery_usd
        2940.0
    """
    logger.info(
        "RecoveryOracle: scoring case_id=%s  overall_strength=%s  violations=%d",
        case.case_id,
        verdict.overall_strength,
        len(verdict.violations),
    )

    ctx = ScoringContext(verdict=verdict, case=case)
    result = _agent.run_sync(RECOVERY_ORACLE_USER_PROMPT, deps=ctx)
    llm_score: _RecoveryScoreFromLLM = result.output

    # Derive expected_recovery_usd in Python — LLM does not compute arithmetic.
    expected_usd = round(llm_score.recovery_probability * case.invoice.total_amount_usd, 2)

    logger.info(
        "RecoveryOracle: case_id=%s  probability=%.2f  expected_usd=%.2f  action=%s  conf=%.2f",
        case.case_id,
        llm_score.recovery_probability,
        expected_usd,
        llm_score.recommended_action,
        llm_score.confidence,
    )

    return RecoveryScore(
        case_id=llm_score.case_id,
        recovery_probability=llm_score.recovery_probability,
        expected_recovery_usd=expected_usd,
        recommended_action=llm_score.recommended_action,
        confidence=llm_score.confidence,
        reasoning=llm_score.reasoning,
        key_factors=llm_score.key_factors,
    )
