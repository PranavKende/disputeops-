"""Integration tests for RecoveryOracle — Groups 6-8.

These tests run the full score_recovery() function with:
  - ComplianceAuditor executed against real fixtures (LLM mocked via pytest-mock)
  - RecoveryOracle LLM injected via _agent.override(TestModel(custom_output_args=...))

No real LLM API calls are made in any test.

Test inventory (24 tests)
--------------------------
  Group 6 — End-to-end through ComplianceAuditor fixtures (3 × 3 = 9)
    clean_violation: probability >= 0.75, action == auto_file, expected_usd >= $2500
    valid_charge: probability <= 0.20, action == write_off, expected_usd <= $130
    borderline: 0.30 <= probability <= 0.65, action == human_review, usd in [$540, $1170]
    (3 assertions per fixture run, fixture run happens once via session-scoped helper)

  Group 7 — Cross-fixture invariants (5)
    All three produce frozen RecoveryScore
    case_id echoes correctly across all three
    All three have key_factors with 1–5 entries
    All three have non-empty reasoning
    Deterministic: same fixture + same TestModel response twice = identical scores

  Group 8 — Performance (2)
    Single score_recovery call < 1s with TestModel
    Three fixture scorings < 3s total
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from agents.compliance_auditor.compliance_auditor import run_audit
from agents.recovery_oracle import score_recovery
from agents.recovery_oracle.recovery_oracle import _agent
from data.fixtures.borderline_case import (
    CHARGE_AMOUNT_USD as BORDERLINE_AMOUNT,
    make_borderline_case,
    make_borderline_llm_responses,
)
from data.fixtures.clean_violation_case import (
    CHARGE_AMOUNT_USD as CLEAN_AMOUNT,
    make_clean_violation_case,
    make_clean_violation_llm_responses,
)
from data.fixtures.valid_charge_case import (
    CHARGE_AMOUNT_USD as VALID_AMOUNT,
    make_valid_charge_case,
    make_valid_charge_llm_responses,
)
from data.schemas.case import DisputeCase
from data.schemas.score import RecoveryScore
from data.schemas.verdict import ComplianceVerdict
from pydantic_ai.models.test import TestModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_LLM_RESPONSE: dict[str, Any] = {
    "violated": False,
    "confidence": 0.85,
    "reasoning": "Default mock: no issue found.",
}


def _mock_compliance_llm(mocker: Any, per_purpose: dict[str, dict[str, Any]]) -> None:
    """Patch get_llm_client in rules.py for ComplianceAuditor integration."""

    def _side_effect(*, purpose: str, **_kwargs: Any) -> MagicMock:
        matched = _DEFAULT_LLM_RESPONSE
        for key, resp in per_purpose.items():
            if key in purpose:
                matched = resp
                break
        mock_client = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = MagicMock(
            violated=matched.get("violated", False),
            confidence=matched.get("confidence", 0.85),
            reasoning=matched.get("reasoning", "Mock reasoning."),
        )
        mock_client.with_structured_output.return_value = mock_chain
        return mock_client

    mocker.patch(
        "agents.compliance_auditor.rules.get_llm_client",
        side_effect=_side_effect,
    )


def _run_pipeline(
    mocker: Any,
    case: DisputeCase,
    ca_responses: dict[str, dict[str, Any]],
    ro_output_args: dict[str, Any],
) -> tuple[ComplianceVerdict, RecoveryScore]:
    """Run ComplianceAuditor → RecoveryOracle with both LLMs mocked."""
    _mock_compliance_llm(mocker, ca_responses)
    verdict = run_audit(case)
    with _agent.override(model=TestModel(custom_output_args=ro_output_args)):
        score = score_recovery(verdict, case)
    return verdict, score


# Per-fixture TestModel responses — designed to reflect what a competent
# agent would produce given the actual ComplianceAuditor output for each fixture.

_CLEAN_RO_ARGS: dict[str, Any] = {
    "case_id": "DEMO-CVF-2026-0001",
    "recovery_probability": 0.87,  # strong: 6 violations including R001+R007
    "recommended_action": "auto_file",
    "confidence": 0.90,
    "reasoning": (
        "The case presents six independent FMC Part 541 violations including "
        "a decisive R001 invoicing-window breach (47 days, exceeding the 30-day "
        "§541.7(a) limit) and an R007 force-majeure certification failure tied to "
        "a documented ILWU work stoppage. Recovery probability is high."
    ),
    "key_factors": [
        "R001 invoicing window violation (conf=1.0, 47 days)",
        "R007 force majeure — ILWU stoppage (conf=0.92)",
        "R004 free-time shortfall 24h (conf=1.0)",
        "R011 + R012 billing format violations",
        "Positive: invoice amount $3,920 above carrier write-off threshold",
    ],
}

_VALID_RO_ARGS: dict[str, Any] = {
    "case_id": "DEMO-VCC-2026-0002",
    "recovery_probability": 0.05,  # no_merit: zero violations
    "recommended_action": "write_off",
    "confidence": 0.88,
    "reasoning": (
        "ComplianceAuditor found no FMC Part 541 violations. All 11 evaluated "
        "rules passed. The charge appears correctly billed and timely issued. "
        "Filing a dispute would have no regulatory basis."
    ),
    "key_factors": [
        "Negative: overall_strength = no_merit",
        "Negative: zero violations across all 11 evaluated rules",
        "Positive: R007 cannot_evaluate — no weather evidence (gap, not exoneration)",
    ],
}

_BORDERLINE_RO_ARGS: dict[str, Any] = {
    "case_id": "DEMO-BDL-2026-0003",
    "recovery_probability": 0.48,  # moderate: 1 violation but 3 cannot_evaluate
    "recommended_action": "human_review",
    "confidence": 0.76,
    "reasoning": (
        "One R005 gate-timestamp violation was found (18-minute discrepancy "
        "beyond the 15-minute tolerance), but three rules could not be evaluated "
        "due to evidence gaps (R004, R006, R007). Recovery probability is moderate; "
        "additional evidence could strengthen the case substantially."
    ),
    "key_factors": [
        "R005 gate-timestamp discrepancy (conf=1.0)",
        "Negative: R004 cannot_evaluate — tariff free-time hours missing",
        "Negative: R007 cannot_evaluate — weather evidence present but inconclusive",
        "Negative: R006 cannot_evaluate — no appointment records",
        "Positive: invoice amount $1,800 above write-off threshold",
    ],
}


# ---------------------------------------------------------------------------
# Group 6 — End-to-end through ComplianceAuditor fixtures
# ---------------------------------------------------------------------------


class TestEndToEndCleanViolation:
    """clean_violation_case: 6 violations, overall_strength='strong', $3,920."""

    @pytest.fixture(autouse=True)
    def _run(self, mocker: Any) -> None:
        case = make_clean_violation_case()
        _, self.score = _run_pipeline(
            mocker, case, make_clean_violation_llm_responses(), _CLEAN_RO_ARGS
        )

    def test_recovery_probability_above_threshold(self) -> None:
        assert self.score.recovery_probability >= 0.75

    def test_recommended_action_is_auto_file(self) -> None:
        assert self.score.recommended_action == "auto_file"

    def test_expected_recovery_usd_above_minimum(self) -> None:
        assert self.score.expected_recovery_usd >= 2500.0


class TestEndToEndValidCharge:
    """valid_charge_case: 0 violations, overall_strength='no_merit', $640."""

    @pytest.fixture(autouse=True)
    def _run(self, mocker: Any) -> None:
        case = make_valid_charge_case()
        _, self.score = _run_pipeline(
            mocker, case, make_valid_charge_llm_responses(), _VALID_RO_ARGS
        )

    def test_recovery_probability_below_threshold(self) -> None:
        assert self.score.recovery_probability <= 0.20

    def test_recommended_action_is_write_off(self) -> None:
        assert self.score.recommended_action == "write_off"

    def test_expected_recovery_usd_below_cap(self) -> None:
        assert self.score.expected_recovery_usd <= 130.0


class TestEndToEndBorderline:
    """borderline_case: 1 violation, 3 cannot_evaluate, overall_strength='moderate', $1,800."""

    @pytest.fixture(autouse=True)
    def _run(self, mocker: Any) -> None:
        case = make_borderline_case()
        _, self.score = _run_pipeline(
            mocker, case, make_borderline_llm_responses(), _BORDERLINE_RO_ARGS
        )

    def test_recovery_probability_in_moderate_range(self) -> None:
        assert 0.30 <= self.score.recovery_probability <= 0.65

    def test_recommended_action_is_human_review(self) -> None:
        assert self.score.recommended_action == "human_review"

    def test_expected_recovery_usd_in_range(self) -> None:
        # 0.30 × 1800 = 540; 0.65 × 1800 = 1170
        assert 540.0 <= self.score.expected_recovery_usd <= 1170.0


# ---------------------------------------------------------------------------
# Group 7 — Cross-fixture invariants
# ---------------------------------------------------------------------------


@pytest.fixture
def all_three_scores(mocker: Any) -> list[RecoveryScore]:
    """Run all three fixtures and return scores."""
    scores = []
    for case_fn, ca_fn, ro_args in [
        (make_clean_violation_case, make_clean_violation_llm_responses, _CLEAN_RO_ARGS),
        (make_valid_charge_case, make_valid_charge_llm_responses, _VALID_RO_ARGS),
        (make_borderline_case, make_borderline_llm_responses, _BORDERLINE_RO_ARGS),
    ]:
        case = case_fn()
        _, score = _run_pipeline(mocker, case, ca_fn(), ro_args)
        scores.append(score)
    return scores


def test_all_scores_are_frozen(all_three_scores: list[RecoveryScore]) -> None:
    for score in all_three_scores:
        with pytest.raises(Exception):
            score.recovery_probability = 0.0  # type: ignore[misc]


def test_case_ids_echo_correctly(mocker: Any) -> None:
    cases = [make_clean_violation_case(), make_valid_charge_case(), make_borderline_case()]
    ro_args_list = [_CLEAN_RO_ARGS, _VALID_RO_ARGS, _BORDERLINE_RO_ARGS]
    ca_fn_list = [
        make_clean_violation_llm_responses,
        make_valid_charge_llm_responses,
        make_borderline_llm_responses,
    ]
    for case, ca_fn, ro_args in zip(cases, ca_fn_list, ro_args_list):
        _, score = _run_pipeline(mocker, case, ca_fn(), ro_args)
        assert score.case_id == case.case_id


def test_all_scores_have_valid_key_factors_count(all_three_scores: list[RecoveryScore]) -> None:
    for score in all_three_scores:
        assert 1 <= len(score.key_factors) <= 5


def test_all_scores_have_non_empty_reasoning(all_three_scores: list[RecoveryScore]) -> None:
    for score in all_three_scores:
        assert len(score.reasoning.strip()) > 0


def test_deterministic_same_inputs_produce_same_output(mocker: Any) -> None:
    """Two runs with the same case and same TestModel response produce identical scores."""
    case = make_clean_violation_case()
    _, score1 = _run_pipeline(mocker, case, make_clean_violation_llm_responses(), _CLEAN_RO_ARGS)
    _, score2 = _run_pipeline(mocker, case, make_clean_violation_llm_responses(), _CLEAN_RO_ARGS)
    assert score1 == score2


# ---------------------------------------------------------------------------
# Group 8 — Performance
# ---------------------------------------------------------------------------


def test_single_score_completes_under_one_second(mocker: Any) -> None:
    case = make_clean_violation_case()
    _mock_compliance_llm(mocker, make_clean_violation_llm_responses())
    verdict = run_audit(case)
    start = time.perf_counter()
    with _agent.override(model=TestModel(custom_output_args=_CLEAN_RO_ARGS)):
        score_recovery(verdict, case)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"score_recovery took {elapsed:.2f}s (limit: 1.0s)"


def test_three_fixture_scorings_complete_under_three_seconds(mocker: Any) -> None:
    fixtures = [
        (make_clean_violation_case, make_clean_violation_llm_responses, _CLEAN_RO_ARGS),
        (make_valid_charge_case, make_valid_charge_llm_responses, _VALID_RO_ARGS),
        (make_borderline_case, make_borderline_llm_responses, _BORDERLINE_RO_ARGS),
    ]
    start = time.perf_counter()
    for case_fn, ca_fn, ro_args in fixtures:
        case = case_fn()
        _run_pipeline(mocker, case, ca_fn(), ro_args)
    elapsed = time.perf_counter() - start
    assert elapsed < 3.0, f"Three scorings took {elapsed:.2f}s (limit: 3.0s)"
