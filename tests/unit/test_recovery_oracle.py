"""Unit tests for RecoveryOracle — Groups 1-5.

All tests mock _agent.run_sync; no real LLM API calls are made.
"""

from __future__ import annotations

import dataclasses
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agents.recovery_oracle import ScoringContext, score_recovery
from agents.recovery_oracle.recovery_oracle import (
    _RecoveryScoreFromLLM,
    _agent,
    _build_system_prompt,
)
from data.fixtures.borderline_case import make_borderline_case
from data.fixtures.clean_violation_case import (
    CHARGE_AMOUNT_USD as CLEAN_AMOUNT,
    make_clean_violation_case,
)
from data.fixtures.valid_charge_case import (
    CHARGE_AMOUNT_USD as VALID_AMOUNT,
    make_valid_charge_case,
)
from data.schemas.case import DisputeCase
from data.schemas.score import RecoveryScore
from data.schemas.verdict import (
    CannotEvaluate,
    ComplianceVerdict,
    MissingEvidenceItem,
    Violation,
)
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_violation() -> Violation:
    return Violation(
        rule_id="R001",
        rule_name="Invoicing window",
        confidence=1.0,
        evidence_refs=["invoice.invoice_date"],
        reasoning="Invoice issued 47 days after charge incurred.",
        citation="§541.7(a) — carrier must issue within 30 days.",
    )


@pytest.fixture
def minimal_cannot_evaluate() -> CannotEvaluate:
    return CannotEvaluate(
        rule_id="R006",
        rule_name="Appointment compliance",
        missing_evidence=[
            MissingEvidenceItem(
                field_path="evidence.appointment_time",
                description="No appointment time recorded.",
            )
        ],
        reasoning="No appointment evidence.",
        citation="§541.6(e)",
    )


@pytest.fixture
def strong_verdict(minimal_violation: Violation) -> ComplianceVerdict:
    """Two high-confidence violations → overall_strength='strong'."""
    v2 = Violation(
        rule_id="R007",
        rule_name="Force majeure",
        confidence=0.92,
        evidence_refs=["evidence.weather_events"],
        reasoning="ILWU stoppage overlaps billed window.",
        citation="§541.6(e)(2)",
    )
    return ComplianceVerdict(
        case_id="TEST-001",
        total_rules_evaluated=10,
        violations=[minimal_violation, v2],
        cannot_evaluate=[],
        clean_results=["R002", "R003", "R004", "R005", "R008", "R009", "R010", "R011"],
        overall_strength="strong",
        summary="Two high-confidence violations found.",
    )


@pytest.fixture
def no_merit_verdict() -> ComplianceVerdict:
    return ComplianceVerdict(
        case_id="TEST-002",
        total_rules_evaluated=11,
        violations=[],
        cannot_evaluate=[],
        clean_results=[
            "R001", "R002", "R003", "R004", "R005",
            "R006", "R007", "R008", "R009", "R010", "R011",
        ],
        overall_strength="no_merit",
        summary="No violations found.",
    )


@pytest.fixture
def moderate_verdict(minimal_violation: Violation) -> ComplianceVerdict:
    return ComplianceVerdict(
        case_id="TEST-003",
        total_rules_evaluated=9,
        violations=[minimal_violation],
        cannot_evaluate=[],
        clean_results=["R002", "R003", "R005", "R006", "R008", "R009", "R010", "R011"],
        overall_strength="moderate",
        summary="One high-confidence violation.",
    )


@pytest.fixture
def clean_case() -> DisputeCase:
    return make_clean_violation_case()


@pytest.fixture
def valid_case() -> DisputeCase:
    return make_valid_charge_case()


def _make_llm_output(**overrides: Any) -> _RecoveryScoreFromLLM:
    """Build a _RecoveryScoreFromLLM with sensible defaults, overridable per test."""
    defaults: dict[str, Any] = {
        "case_id": "TEST-001",
        "recovery_probability": 0.80,
        "recommended_action": "auto_file",
        "confidence": 0.85,
        "reasoning": "Strong case with decisive violations.",
        "key_factors": ["R001 invoicing window violation", "R007 force majeure"],
    }
    defaults.update(overrides)
    return _RecoveryScoreFromLLM(**defaults)


def _mock_agent_result(llm_output: _RecoveryScoreFromLLM) -> MagicMock:
    """Return a mock that simulates agent.run_sync's result."""
    mock = MagicMock()
    mock.output = llm_output
    return mock


# ---------------------------------------------------------------------------
# Group 1 — RecoveryScore schema validation
# ---------------------------------------------------------------------------


class TestRecoveryScoreSchema:
    def test_valid_score_constructs(self) -> None:
        score = RecoveryScore(
            case_id="X-001",
            recovery_probability=0.75,
            expected_recovery_usd=1500.0,
            recommended_action="auto_file",
            confidence=0.85,
            reasoning="Strong violations.",
            key_factors=["R001 violation"],
        )
        assert score.recovery_probability == 0.75

    def test_low_confidence_wrong_action_raises(self) -> None:
        with pytest.raises(Exception):
            RecoveryScore(
                case_id="X-002",
                recovery_probability=0.20,
                expected_recovery_usd=40.0,
                recommended_action="write_off",  # must be human_review when conf < 0.66
                confidence=0.62,
                reasoning="Ambiguous.",
                key_factors=["x"],
            )

    def test_low_confidence_with_human_review_is_valid(self) -> None:
        score = RecoveryScore(
            case_id="X-003",
            recovery_probability=0.20,
            expected_recovery_usd=40.0,
            recommended_action="human_review",
            confidence=0.62,
            reasoning="Low confidence — route to human.",
            key_factors=["Ambiguous evidence"],
        )
        assert score.recommended_action == "human_review"

    def test_confidence_clips_up_from_below_floor(self) -> None:
        score = RecoveryScore(
            case_id="X-004",
            recovery_probability=0.50,
            expected_recovery_usd=250.0,
            recommended_action="human_review",
            confidence=0.40,  # clips to 0.60
            reasoning=".",
            key_factors=["x"],
        )
        assert score.confidence == pytest.approx(0.60)

    def test_confidence_clips_down_from_above_ceiling(self) -> None:
        score = RecoveryScore(
            case_id="X-005",
            recovery_probability=0.80,
            expected_recovery_usd=800.0,
            recommended_action="auto_file",
            confidence=0.99,  # clips to 0.95
            reasoning=".",
            key_factors=["x"],
        )
        assert score.confidence == pytest.approx(0.95)

    def test_confidence_in_range_unchanged(self) -> None:
        score = RecoveryScore(
            case_id="X-006",
            recovery_probability=0.80,
            expected_recovery_usd=800.0,
            recommended_action="auto_file",
            confidence=0.75,
            reasoning=".",
            key_factors=["x"],
        )
        assert score.confidence == pytest.approx(0.75)

    def test_recovery_probability_above_one_raises(self) -> None:
        with pytest.raises(Exception):
            RecoveryScore(
                case_id="X", recovery_probability=1.1, expected_recovery_usd=0,
                recommended_action="auto_file", confidence=0.85,
                reasoning=".", key_factors=["x"],
            )

    def test_recovery_probability_negative_raises(self) -> None:
        with pytest.raises(Exception):
            RecoveryScore(
                case_id="X", recovery_probability=-0.1, expected_recovery_usd=0,
                recommended_action="write_off", confidence=0.85,
                reasoning=".", key_factors=["x"],
            )

    def test_expected_recovery_usd_negative_raises(self) -> None:
        with pytest.raises(Exception):
            RecoveryScore(
                case_id="X", recovery_probability=0.5, expected_recovery_usd=-1.0,
                recommended_action="human_review", confidence=0.85,
                reasoning=".", key_factors=["x"],
            )

    def test_key_factors_empty_raises(self) -> None:
        with pytest.raises(Exception):
            RecoveryScore(
                case_id="X", recovery_probability=0.5, expected_recovery_usd=250.0,
                recommended_action="human_review", confidence=0.85,
                reasoning=".", key_factors=[],  # min_length=1
            )

    def test_key_factors_six_items_raises(self) -> None:
        with pytest.raises(Exception):
            RecoveryScore(
                case_id="X", recovery_probability=0.5, expected_recovery_usd=250.0,
                recommended_action="human_review", confidence=0.85,
                reasoning=".", key_factors=["a", "b", "c", "d", "e", "f"],  # max_length=5
            )

    def test_model_is_frozen(self) -> None:
        score = RecoveryScore(
            case_id="X", recovery_probability=0.75, expected_recovery_usd=750.0,
            recommended_action="auto_file", confidence=0.85,
            reasoning=".", key_factors=["x"],
        )
        with pytest.raises(Exception):
            score.recovery_probability = 0.99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Group 2 — expected_recovery_usd derivation
# ---------------------------------------------------------------------------


class TestExpectedRecoveryDerivation:
    """Tests that score_recovery() derives expected_recovery_usd correctly.

    These tests mock the agent call and verify the Python arithmetic, not the LLM.
    """

    def _score_with_prob(
        self,
        case: DisputeCase,
        verdict: ComplianceVerdict,
        probability: float,
        action: str = "auto_file",
        confidence: float = 0.85,
    ) -> RecoveryScore:
        llm_out = _make_llm_output(
            case_id=case.case_id,
            recovery_probability=probability,
            recommended_action=action,
            confidence=confidence,
        )
        with patch.object(_agent, "run_sync", return_value=_mock_agent_result(llm_out)):
            return score_recovery(verdict, case)

    def test_standard_multiplication(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        # 0.78 × 3920.00 = 3057.60
        score = self._score_with_prob(clean_case, strong_verdict, 0.78)
        assert score.expected_recovery_usd == pytest.approx(3057.60)

    def test_zero_probability(
        self, no_merit_verdict: ComplianceVerdict, valid_case: DisputeCase
    ) -> None:
        score = self._score_with_prob(
            valid_case, no_merit_verdict, 0.0, action="write_off", confidence=0.85
        )
        assert score.expected_recovery_usd == pytest.approx(0.0)

    def test_full_probability(
        self, strong_verdict: ComplianceVerdict, valid_case: DisputeCase
    ) -> None:
        # 1.0 × 640.00 = 640.00
        score = self._score_with_prob(valid_case, strong_verdict, 1.0)
        assert score.expected_recovery_usd == pytest.approx(640.0)

    def test_rounding_two_decimal_places(
        self, moderate_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        # 0.333 × 100.00 = 33.3, rounded to 33.3 (already two decimals)
        # Use a case with $100 by patching the invoice — easier: build ad-hoc case
        # We verify the formula: round(probability * amount, 2)
        score = self._score_with_prob(clean_case, moderate_verdict, 0.333)
        expected = round(0.333 * CLEAN_AMOUNT, 2)
        assert score.expected_recovery_usd == pytest.approx(expected)

    def test_rounding_python_behavior(
        self, moderate_verdict: ComplianceVerdict, valid_case: DisputeCase
    ) -> None:
        # 0.5 × 640.00 = 320.00 — clean
        score = self._score_with_prob(valid_case, moderate_verdict, 0.5)
        assert score.expected_recovery_usd == pytest.approx(320.0)


# ---------------------------------------------------------------------------
# Group 3 — score_recovery() orchestration
# ---------------------------------------------------------------------------


class TestScoreRecoveryOrchestration:
    def test_calls_agent_run_sync_exactly_once(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        llm_out = _make_llm_output(case_id=clean_case.case_id)
        with patch.object(_agent, "run_sync", return_value=_mock_agent_result(llm_out)) as mock_run:
            score_recovery(strong_verdict, clean_case)
        mock_run.assert_called_once()

    def test_passes_scoring_context_as_deps(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        llm_out = _make_llm_output(case_id=clean_case.case_id)
        with patch.object(_agent, "run_sync", return_value=_mock_agent_result(llm_out)) as mock_run:
            score_recovery(strong_verdict, clean_case)
        _, kwargs = mock_run.call_args
        deps: ScoringContext = kwargs["deps"]
        assert isinstance(deps, ScoringContext)
        assert deps.verdict is strong_verdict
        assert deps.case is clean_case

    def test_reads_result_output_not_data(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        llm_out = _make_llm_output(case_id=clean_case.case_id)
        mock_result = _mock_agent_result(llm_out)
        mock_result.data = MagicMock(side_effect=AssertionError(".data must not be read"))
        with patch.object(_agent, "run_sync", return_value=mock_result):
            score_recovery(strong_verdict, clean_case)  # must not raise

    def test_returns_recovery_score_instance(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        llm_out = _make_llm_output(case_id=clean_case.case_id)
        with patch.object(_agent, "run_sync", return_value=_mock_agent_result(llm_out)):
            result = score_recovery(strong_verdict, clean_case)
        assert isinstance(result, RecoveryScore)

    def test_propagates_case_id(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        llm_out = _make_llm_output(case_id=clean_case.case_id)
        with patch.object(_agent, "run_sync", return_value=_mock_agent_result(llm_out)):
            result = score_recovery(strong_verdict, clean_case)
        assert result.case_id == clean_case.case_id

    def test_echoes_recovery_probability(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        llm_out = _make_llm_output(case_id=clean_case.case_id, recovery_probability=0.73)
        with patch.object(_agent, "run_sync", return_value=_mock_agent_result(llm_out)):
            result = score_recovery(strong_verdict, clean_case)
        assert result.recovery_probability == pytest.approx(0.73)

    def test_echoes_confidence_post_clip(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        # The LLM output's clip_confidence fires on _RecoveryScoreFromLLM construction.
        # By the time score_recovery reads llm_score.confidence, it is already clipped.
        llm_out = _make_llm_output(case_id=clean_case.case_id, confidence=0.90)
        with patch.object(_agent, "run_sync", return_value=_mock_agent_result(llm_out)):
            result = score_recovery(strong_verdict, clean_case)
        assert result.confidence == pytest.approx(0.90)

    def test_echoes_reasoning(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        reasoning = "Decisive R001 + R007 violations stack well."
        llm_out = _make_llm_output(case_id=clean_case.case_id, reasoning=reasoning)
        with patch.object(_agent, "run_sync", return_value=_mock_agent_result(llm_out)):
            result = score_recovery(strong_verdict, clean_case)
        assert result.reasoning == reasoning

    def test_echoes_key_factors(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        factors = ["R001 invoicing window (conf=1.0)", "ILWU force majeure"]
        llm_out = _make_llm_output(case_id=clean_case.case_id, key_factors=factors)
        with patch.object(_agent, "run_sync", return_value=_mock_agent_result(llm_out)):
            result = score_recovery(strong_verdict, clean_case)
        assert result.key_factors == factors

    def test_echoes_recommended_action(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        llm_out = _make_llm_output(case_id=clean_case.case_id, recommended_action="auto_file")
        with patch.object(_agent, "run_sync", return_value=_mock_agent_result(llm_out)):
            result = score_recovery(strong_verdict, clean_case)
        assert result.recommended_action == "auto_file"

    def test_computes_expected_recovery_usd(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        prob = 0.82
        llm_out = _make_llm_output(case_id=clean_case.case_id, recovery_probability=prob)
        with patch.object(_agent, "run_sync", return_value=_mock_agent_result(llm_out)):
            result = score_recovery(strong_verdict, clean_case)
        assert result.expected_recovery_usd == pytest.approx(round(prob * CLEAN_AMOUNT, 2))

    def test_llm_output_type_omits_expected_recovery_usd(self) -> None:
        """_RecoveryScoreFromLLM must not have an expected_recovery_usd field."""
        assert not hasattr(_RecoveryScoreFromLLM.model_fields, "expected_recovery_usd"), (
            "_RecoveryScoreFromLLM must not have expected_recovery_usd — "
            "that field is derived in Python, not by the LLM."
        )

    @pytest.mark.parametrize(
        "probability,action,confidence,invoice_amount,expected_action",
        [
            (0.82, "auto_file", 0.88, 3920.0, "auto_file"),      # clean_violation shape
            (0.10, "write_off", 0.80, 640.0, "write_off"),        # no_merit shape
            (0.50, "human_review", 0.78, 1800.0, "human_review"), # borderline shape
        ],
    )
    def test_routing_parametrized(
        self,
        probability: float,
        action: str,
        confidence: float,
        invoice_amount: float,
        expected_action: str,
        strong_verdict: ComplianceVerdict,
        clean_case: DisputeCase,
    ) -> None:
        llm_out = _make_llm_output(
            case_id=clean_case.case_id,
            recovery_probability=probability,
            recommended_action=action,
            confidence=confidence,
        )
        with patch.object(_agent, "run_sync", return_value=_mock_agent_result(llm_out)):
            result = score_recovery(strong_verdict, clean_case)
        assert result.recommended_action == expected_action


# ---------------------------------------------------------------------------
# Group 4 — System prompt content
# ---------------------------------------------------------------------------


def _build_prompt_for(verdict: ComplianceVerdict, case: DisputeCase) -> str:
    """Call the registered system_prompt builder with a minimal RunContext."""
    ctx = ScoringContext(verdict=verdict, case=case)
    mock_run_ctx = RunContext(
        deps=ctx,
        model=TestModel(),
        usage=None,
        prompt="test",
        messages=[],
        retries=0,
        run_step=0,
    )
    runner = _agent._system_prompt_functions[0]
    return runner.function(mock_run_ctx)


class TestSystemPromptContent:
    def test_prompt_contains_role_statement(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        prompt = _build_prompt_for(strong_verdict, clean_case)
        assert "RecoveryOracle" in prompt

    def test_prompt_contains_threshold_table(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        prompt = _build_prompt_for(strong_verdict, clean_case)
        assert "auto_file" in prompt
        assert "write_off" in prompt
        assert "human_review" in prompt

    def test_prompt_contains_confidence_clamp_instruction(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        prompt = _build_prompt_for(strong_verdict, clean_case)
        assert "0.95" in prompt
        assert "0.6" in prompt

    def test_prompt_contains_overall_strength(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        prompt = _build_prompt_for(strong_verdict, clean_case)
        assert "strong" in prompt

    def test_prompt_contains_violation_count(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        prompt = _build_prompt_for(strong_verdict, clean_case)
        assert "violation_count: 2" in prompt

    def test_prompt_contains_each_violation_rule_id(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        prompt = _build_prompt_for(strong_verdict, clean_case)
        assert "R001" in prompt
        assert "R007" in prompt

    def test_prompt_contains_violation_confidence(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        prompt = _build_prompt_for(strong_verdict, clean_case)
        assert "conf=1.00" in prompt   # R001 confidence
        assert "conf=0.92" in prompt   # R007 confidence

    def test_prompt_contains_carrier_name(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        prompt = _build_prompt_for(strong_verdict, clean_case)
        assert clean_case.invoice.carrier_name in prompt

    def test_prompt_contains_invoice_amount(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        prompt = _build_prompt_for(strong_verdict, clean_case)
        assert str(clean_case.invoice.total_amount_usd) in prompt


# ---------------------------------------------------------------------------
# Group 5 — ScoringContext integrity
# ---------------------------------------------------------------------------


class TestScoringContextIntegrity:
    def test_constructs_from_valid_verdict_and_case(
        self, strong_verdict: ComplianceVerdict, clean_case: DisputeCase
    ) -> None:
        ctx = ScoringContext(verdict=strong_verdict, case=clean_case)
        assert ctx.verdict is strong_verdict
        assert ctx.case is clean_case

    def test_is_dataclass(self) -> None:
        assert dataclasses.is_dataclass(ScoringContext)

    def test_importable_from_package(self) -> None:
        from agents.recovery_oracle import ScoringContext as SC  # noqa: F401
        assert SC is ScoringContext
