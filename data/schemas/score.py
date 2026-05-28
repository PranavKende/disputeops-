"""Output schema for RecoveryOracle's recovery probability score.

This model is produced by RecoveryOracle (agents/recovery_oracle/) and consumed
downstream by:
  - RouteMatrix (UiPath DMN): reads recommended_action + recovery_probability
  - DemandSmith (coded agent): reads reasoning + key_factors for letter context
  - RecoveryPulse (UiPath Process Intelligence): aggregates scores for telemetry

Design note — expected_recovery_usd
------------------------------------
expected_recovery_usd is derived in Python by RecoveryOracle after the LLM call,
not computed by the LLM.  The formula is:
    expected_recovery_usd = round(recovery_probability × invoice.total_amount_usd, 2)
This prevents arithmetic hallucination — the LLM is responsible for the probability
estimate (a reasoning task); Python is responsible for the multiplication (a math task).
The internal _RecoveryScoreFromLLM model in recovery_oracle.py omits this field.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RecoveryScore(BaseModel):
    """Recovery probability score produced by RecoveryOracle.

    Attributes:
        case_id: Case ID echoed from the input DisputeCase, for correlation.
        recovery_probability: Estimated probability the dispute recovers any
            amount, expressed as a float in [0.0, 1.0].
        expected_recovery_usd: Probability-weighted recovery amount in USD.
            Derived in Python as round(recovery_probability × total_amount_usd, 2),
            not LLM-computed.  See module docstring.
        recommended_action: Routing recommendation:
            - ``auto_file``: probability >= 0.75 AND invoice amount >= $500.
            - ``write_off``: probability < 0.40 AND invoice amount < $500.
            - ``human_review``: all other cases, plus any low-confidence score.
        confidence: Agent's confidence in its own score, clipped to [0.6, 0.95].
            RecoveryOracle never claims certainty about a probabilistic estimate.
            Values below 0.60 are clipped up to 0.60; values above 0.95 are
            clipped down to 0.95.  If the clipped confidence is below 0.66,
            recommended_action is forced to ``human_review`` regardless of
            the probability number.
        reasoning: Plain-English justification for the score, 2–4 sentences,
            written to be demand-letter-ready.  Should name the specific
            violations driving the score.
        key_factors: Top 3–5 factors driving the score (positive and negative),
            as short noun phrases.  Used by DemandSmith for letter framing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(
        ...,
        description="Case ID echoed from DisputeCase, for downstream correlation",
    )
    recovery_probability: Annotated[float, Field(ge=0.0, le=1.0)] = Field(
        ...,
        description="Estimated probability of recovering any amount [0.0–1.0]",
    )
    expected_recovery_usd: Annotated[float, Field(ge=0.0)] = Field(
        ...,
        description=(
            "Probability-weighted recovery in USD. "
            "Derived as round(recovery_probability × invoice.total_amount_usd, 2). "
            "Not LLM-computed — see module docstring."
        ),
    )
    recommended_action: Literal["auto_file", "human_review", "write_off"] = Field(
        ...,
        description=(
            "auto_file: p>=0.75 and amount>=$500. "
            "write_off: p<0.40 and amount<$500. "
            "human_review: all other cases, and any low-confidence score."
        ),
    )
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = Field(
        ...,
        description=(
            "Agent confidence in its own score [0.0–1.0]. "
            "Clipped to [0.6, 0.95] by model_validator before other validation. "
            "Effective floor is 0.60; effective ceiling is 0.95."
        ),
    )
    reasoning: str = Field(
        ...,
        description="Plain-English justification, 2–4 sentences, demand-letter-ready",
    )
    key_factors: list[str] = Field(
        ...,
        min_length=1,
        max_length=5,
        description="Top 3–5 factors driving the score (positive and negative)",
    )

    @model_validator(mode="before")
    @classmethod
    def clip_confidence(cls, data: object) -> object:
        """Clip confidence into [0.6, 0.95] before field validation runs.

        The LLM may return a confidence outside this range (e.g. 0.55 for
        genuinely ambiguous cases, or 1.0 for overconfident responses).
        Rather than failing validation and triggering a retry, we silently
        clip — the clipped value is semantically correct (minimum meaningful
        confidence; maximum claimable confidence) and consistent with the
        convention used by ComplianceAuditor's LLM-assisted rules.
        """
        if isinstance(data, dict) and "confidence" in data:
            raw = data["confidence"]
            if isinstance(raw, (int, float)):
                data = {**data, "confidence": float(max(0.6, min(0.95, raw)))}
        return data

    @model_validator(mode="after")
    def low_confidence_forces_human_review(self) -> "RecoveryScore":
        """Force human_review when confidence is at or near floor.

        Threshold is 0.66 (not 0.65) so that a clipped confidence of exactly
        0.60 is unambiguously caught without floating-point edge cases.
        """
        if self.confidence < 0.66 and self.recommended_action != "human_review":
            raise ValueError(
                f"confidence={self.confidence:.3f} is below 0.66 threshold; "
                f"recommended_action must be 'human_review', "
                f"got '{self.recommended_action}'."
            )
        return self
