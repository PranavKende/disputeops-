"""Integration tests for ComplianceAuditor end-to-end audits.

These tests run the full LangGraph agent against the three demo fixtures
(clean_violation, valid_charge, borderline) and verify the resulting
ComplianceVerdict matches the expected contracts in expected_verdicts.py.

All LLM calls are intercepted via a per-purpose mock helper that consults the
fixture's ``make_*_llm_responses()`` dict.  No real API calls are made.

Test inventory (22 tests)
-------------------------
  Fixture-parametrized (3 × 2 = 6)
    IT-01  overall_strength matches expected
    IT-02  total_rules_evaluated matches expected

  Clean violation fixture (7)
    IT-03  all six violation rule IDs present
    IT-04  exactly six violations
    IT-05  R006 is cannot_evaluate (no appointment_time)
    IT-06  summary contains carrier, container, amount, "violation(s)"
    IT-07  summary does NOT contain "no FMC Part 541"
    IT-08  violations list rule IDs in canonical order (R001 before R012)
    IT-09  clean_results does not include any violation rule ID

  Valid charge fixture (5)
    IT-10  violations list is empty
    IT-11  R007 is cannot_evaluate (no weather_events triggers evidence gate)
    IT-12  summary contains "no FMC Part 541"
    IT-13  summary does NOT contain "violation(s) identified"
    IT-14  total_rules_evaluated == 11

  Borderline fixture (5)
    IT-15  R005 is the sole violation
    IT-16  R004, R006, R007 all in cannot_evaluate
    IT-17  total_rules_evaluated == 9
    IT-18  summary contains carrier, container, comma-formatted amount

  Cross-fixture invariants (3)
    IT-19  all verdicts: total_rules_evaluated == len(violations) + len(clean_results)
    IT-20  all verdicts: cannot_evaluate rule IDs are disjoint from violations
    IT-21  all verdicts: comma-formatted ${:,.2f} amount in summary

  Performance (1)
    IT-22  run_audit completes in under 10 seconds (sync, mocked LLM)
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from agents.compliance_auditor.compliance_auditor import run_audit
from data.fixtures.borderline_case import (
    CHARGE_AMOUNT_USD as BORDERLINE_AMOUNT,
    CONTAINER_NUMBER as BORDERLINE_CONTAINER,
    make_borderline_case,
    make_borderline_llm_responses,
)
from data.fixtures.clean_violation_case import (
    CHARGE_AMOUNT_USD as CLEAN_AMOUNT,
    CONTAINER_NUMBER as CLEAN_CONTAINER,
    make_clean_violation_case,
    make_clean_violation_llm_responses,
)
from data.fixtures.expected_verdicts import (
    BORDERLINE_EXPECTED,
    CLEAN_VIOLATION_EXPECTED,
    VALID_CHARGE_EXPECTED,
    ExpectedVerdict,
)
from data.fixtures.valid_charge_case import (
    CHARGE_AMOUNT_USD as VALID_AMOUNT,
    CONTAINER_NUMBER as VALID_CONTAINER,
    make_valid_charge_case,
    make_valid_charge_llm_responses,
)
from data.schemas.case import DisputeCase
from data.schemas.verdict import ComplianceVerdict


# ---------------------------------------------------------------------------
# Mock helper
# ---------------------------------------------------------------------------

_DEFAULT_LLM_RESPONSE = {
    "violated": False,
    "confidence": 0.85,
    "reasoning": "Default mock: no issue found.",
}


def _build_mock_llm_client(
    mocker,
    per_purpose_responses: dict[str, dict],
) -> MagicMock:
    """Return a MagicMock for get_llm_client that dispatches per purpose.

    When ``get_llm_client(purpose=p)`` is called, the returned chain's
    ``invoke()`` returns the first entry in ``per_purpose_responses`` whose
    key is a substring of ``p``.  Falls back to ``_DEFAULT_LLM_RESPONSE``.
    """

    def _side_effect(*, purpose: str, **_kwargs: Any) -> MagicMock:
        matched_resp = _DEFAULT_LLM_RESPONSE
        for key, resp in per_purpose_responses.items():
            if key in purpose:
                matched_resp = resp
                break

        mock_client = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = MagicMock(
            violated=matched_resp.get("violated", False),
            confidence=matched_resp.get("confidence", 0.85),
            reasoning=matched_resp.get("reasoning", "Mock reasoning."),
        )
        mock_client.with_structured_output.return_value = mock_chain
        return mock_client

    mocker.patch(
        "agents.compliance_auditor.rules.get_llm_client",
        side_effect=_side_effect,
    )


def _run_with_mocks(
    mocker,
    case: DisputeCase,
    per_purpose_responses: dict[str, dict],
) -> ComplianceVerdict:
    """Run the full audit with mocked LLM calls and return the verdict."""
    _build_mock_llm_client(mocker, per_purpose_responses)
    return run_audit(case)


# ---------------------------------------------------------------------------
# Fixture parametrize data
# ---------------------------------------------------------------------------

FIXTURE_PARAMS = [
    pytest.param(
        make_clean_violation_case,
        make_clean_violation_llm_responses,
        CLEAN_VIOLATION_EXPECTED,
        id="clean_violation",
    ),
    pytest.param(
        make_valid_charge_case,
        make_valid_charge_llm_responses,
        VALID_CHARGE_EXPECTED,
        id="valid_charge",
    ),
    pytest.param(
        make_borderline_case,
        make_borderline_llm_responses,
        BORDERLINE_EXPECTED,
        id="borderline",
    ),
]


# ---------------------------------------------------------------------------
# IT-01 / IT-02 — parametrized across all fixtures
# ---------------------------------------------------------------------------

class TestAllFixtures:
    """Tests that run against every fixture via parametrize."""

    @pytest.mark.parametrize("make_case,make_llm,expected", FIXTURE_PARAMS)
    def test_overall_strength_matches_expected(
        self,
        mocker,
        make_case,
        make_llm,
        expected: ExpectedVerdict,
    ) -> None:
        """IT-01: overall_strength matches the fixture contract."""
        verdict = _run_with_mocks(mocker, make_case(), make_llm())
        assert verdict.overall_strength == expected.overall_strength, (
            f"Expected overall_strength={expected.overall_strength!r} "
            f"but got {verdict.overall_strength!r}"
        )

    @pytest.mark.parametrize("make_case,make_llm,expected", FIXTURE_PARAMS)
    def test_total_rules_evaluated_matches_expected(
        self,
        mocker,
        make_case,
        make_llm,
        expected: ExpectedVerdict,
    ) -> None:
        """IT-02: total_rules_evaluated equals len(violations) + len(clean_results)."""
        verdict = _run_with_mocks(mocker, make_case(), make_llm())
        assert verdict.total_rules_evaluated == expected.expected_total_rules_evaluated, (
            f"Expected total_rules_evaluated={expected.expected_total_rules_evaluated} "
            f"but got {verdict.total_rules_evaluated} "
            f"(violations={[v.rule_id for v in verdict.violations]}, "
            f"clean={verdict.clean_results})"
        )


# ---------------------------------------------------------------------------
# IT-03 … IT-09 — Clean violation fixture
# ---------------------------------------------------------------------------

class TestCleanViolationCase:
    """Empire Freight Lines / Pacifica Apparel — strong, 6 violations."""

    @pytest.fixture(autouse=True)
    def _run(self, mocker) -> None:
        self.verdict = _run_with_mocks(
            mocker,
            make_clean_violation_case(),
            make_clean_violation_llm_responses(),
        )

    def test_all_six_violation_rule_ids_present(self) -> None:
        """IT-03: R001, R002, R004, R007, R011, R012 all in violations."""
        found_ids = {v.rule_id for v in self.verdict.violations}
        for rule_id in CLEAN_VIOLATION_EXPECTED.expected_violations:
            assert rule_id in found_ids, (
                f"Expected violation {rule_id} not found. "
                f"Actual violations: {sorted(found_ids)}"
            )

    def test_exactly_six_violations(self) -> None:
        """IT-04: exactly 6 violations, not more, not fewer."""
        assert len(self.verdict.violations) == 6, (
            f"Expected 6 violations, got {len(self.verdict.violations)}: "
            f"{[v.rule_id for v in self.verdict.violations]}"
        )

    def test_r006_is_cannot_evaluate(self) -> None:
        """IT-05: R006 is cannot_evaluate because appointment_time is None."""
        ce_ids = {ce.rule_id for ce in self.verdict.cannot_evaluate}
        assert "R006" in ce_ids, (
            f"Expected R006 in cannot_evaluate, got: {sorted(ce_ids)}"
        )

    def test_summary_contains_required_strings(self) -> None:
        """IT-06: summary contains carrier, container, amount, 'violation(s)'."""
        for expected_str in CLEAN_VIOLATION_EXPECTED.summary_must_contain:
            assert expected_str in self.verdict.summary, (
                f"Expected {expected_str!r} in summary.\n"
                f"Actual summary: {self.verdict.summary!r}"
            )

    def test_summary_does_not_contain_no_violations_phrase(self) -> None:
        """IT-07: summary must NOT contain 'no FMC Part 541'."""
        for forbidden in CLEAN_VIOLATION_EXPECTED.summary_must_not_contain:
            assert forbidden not in self.verdict.summary, (
                f"Forbidden string {forbidden!r} found in summary.\n"
                f"Actual summary: {self.verdict.summary!r}"
            )

    def test_violations_in_canonical_order(self) -> None:
        """IT-08: violation rule IDs appear in ascending numeric order."""
        ids = [v.rule_id for v in self.verdict.violations]
        assert ids == sorted(ids), (
            f"Violations not in canonical order: {ids}"
        )

    def test_clean_results_exclude_violation_ids(self) -> None:
        """IT-09: clean_results does not include any violation rule ID."""
        violation_ids = {v.rule_id for v in self.verdict.violations}
        for rule_id in self.verdict.clean_results:
            assert rule_id not in violation_ids, (
                f"Rule {rule_id} is in both violations and clean_results"
            )


# ---------------------------------------------------------------------------
# IT-10 … IT-14 — Valid charge fixture
# ---------------------------------------------------------------------------

class TestValidChargeCase:
    """Pacific Liner Co. / Westcoast Distribution — no_merit, 0 violations."""

    @pytest.fixture(autouse=True)
    def _run(self, mocker) -> None:
        self.verdict = _run_with_mocks(
            mocker,
            make_valid_charge_case(),
            make_valid_charge_llm_responses(),
        )

    def test_violations_list_is_empty(self) -> None:
        """IT-10: no violations should be found on a valid charge."""
        assert self.verdict.violations == [], (
            f"Expected no violations, got: {[v.rule_id for v in self.verdict.violations]}"
        )

    def test_r007_is_cannot_evaluate(self) -> None:
        """IT-11: R007 cannot_evaluate because weather_events is empty (evidence gate)."""
        ce_ids = {ce.rule_id for ce in self.verdict.cannot_evaluate}
        assert "R007" in ce_ids, (
            f"Expected R007 in cannot_evaluate, got: {sorted(ce_ids)}"
        )

    def test_summary_contains_no_violation_phrase(self) -> None:
        """IT-12: summary contains 'no FMC Part 541' for a passing audit."""
        for expected_str in VALID_CHARGE_EXPECTED.summary_must_contain:
            assert expected_str in self.verdict.summary, (
                f"Expected {expected_str!r} in summary.\n"
                f"Actual summary: {self.verdict.summary!r}"
            )

    def test_summary_does_not_contain_violation_identified_phrase(self) -> None:
        """IT-13: summary must NOT contain 'violation(s) identified'."""
        for forbidden in VALID_CHARGE_EXPECTED.summary_must_not_contain:
            assert forbidden not in self.verdict.summary, (
                f"Forbidden string {forbidden!r} found in summary.\n"
                f"Actual summary: {self.verdict.summary!r}"
            )

    def test_total_rules_evaluated_is_11(self) -> None:
        """IT-14: 11 rules evaluated (R007 cannot_evaluate not counted)."""
        assert self.verdict.total_rules_evaluated == 11, (
            f"Expected total_rules_evaluated=11, got {self.verdict.total_rules_evaluated}"
        )


# ---------------------------------------------------------------------------
# IT-15 … IT-18 — Borderline fixture
# ---------------------------------------------------------------------------

class TestBorderlineCase:
    """Coastal Carrier Group / Heartland Imports — moderate, 1 violation."""

    @pytest.fixture(autouse=True)
    def _run(self, mocker) -> None:
        self.verdict = _run_with_mocks(
            mocker,
            make_borderline_case(),
            make_borderline_llm_responses(),
        )

    def test_r005_is_sole_violation(self) -> None:
        """IT-15: only R005 fires; no other violations."""
        violation_ids = [v.rule_id for v in self.verdict.violations]
        assert violation_ids == ["R005"], (
            f"Expected only [R005] but got {violation_ids}"
        )

    def test_r004_r006_r007_are_cannot_evaluate(self) -> None:
        """IT-16: R004, R006, R007 all in cannot_evaluate."""
        ce_ids = {ce.rule_id for ce in self.verdict.cannot_evaluate}
        for rule_id in ["R004", "R006", "R007"]:
            assert rule_id in ce_ids, (
                f"Expected {rule_id} in cannot_evaluate, got: {sorted(ce_ids)}"
            )

    def test_total_rules_evaluated_is_9(self) -> None:
        """IT-17: 9 rules evaluated (R004, R006, R007 cannot_evaluate not counted)."""
        assert self.verdict.total_rules_evaluated == 9, (
            f"Expected total_rules_evaluated=9, got {self.verdict.total_rules_evaluated}"
        )

    def test_summary_contains_required_strings(self) -> None:
        """IT-18: summary contains carrier, container, comma-formatted amount."""
        for expected_str in BORDERLINE_EXPECTED.summary_must_contain:
            assert expected_str in self.verdict.summary, (
                f"Expected {expected_str!r} in summary.\n"
                f"Actual summary: {self.verdict.summary!r}"
            )


# ---------------------------------------------------------------------------
# IT-19 … IT-21 — Cross-fixture invariants
# ---------------------------------------------------------------------------

class TestCrossFixtureInvariants:
    """Invariants that must hold for every fixture."""

    @pytest.mark.parametrize("make_case,make_llm,_expected", FIXTURE_PARAMS)
    def test_total_rules_evaluated_equals_violations_plus_clean(
        self,
        mocker,
        make_case,
        make_llm,
        _expected: ExpectedVerdict,
    ) -> None:
        """IT-19: total_rules_evaluated == len(violations) + len(clean_results)."""
        verdict = _run_with_mocks(mocker, make_case(), make_llm())
        expected_count = len(verdict.violations) + len(verdict.clean_results)
        assert verdict.total_rules_evaluated == expected_count, (
            f"total_rules_evaluated={verdict.total_rules_evaluated} but "
            f"len(violations)+len(clean_results)={expected_count}"
        )

    @pytest.mark.parametrize("make_case,make_llm,_expected", FIXTURE_PARAMS)
    def test_cannot_evaluate_ids_disjoint_from_violations(
        self,
        mocker,
        make_case,
        make_llm,
        _expected: ExpectedVerdict,
    ) -> None:
        """IT-20: a rule cannot be simultaneously a violation and cannot_evaluate."""
        verdict = _run_with_mocks(mocker, make_case(), make_llm())
        violation_ids = {v.rule_id for v in verdict.violations}
        ce_ids = {ce.rule_id for ce in verdict.cannot_evaluate}
        overlap = violation_ids & ce_ids
        assert overlap == set(), (
            f"Rules in both violations and cannot_evaluate: {overlap}"
        )

    @pytest.mark.parametrize(
        "make_case,make_llm,amount",
        [
            pytest.param(make_clean_violation_case, make_clean_violation_llm_responses,
                         CLEAN_AMOUNT, id="clean_violation"),
            pytest.param(make_valid_charge_case, make_valid_charge_llm_responses,
                         VALID_AMOUNT, id="valid_charge"),
            pytest.param(make_borderline_case, make_borderline_llm_responses,
                         BORDERLINE_AMOUNT, id="borderline"),
        ],
    )
    def test_summary_contains_comma_formatted_amount(
        self,
        mocker,
        make_case,
        make_llm,
        amount: float,
    ) -> None:
        """IT-21: summary always contains the comma-formatted dollar amount."""
        verdict = _run_with_mocks(mocker, make_case(), make_llm())
        formatted = f"${amount:,.2f}"
        assert formatted in verdict.summary, (
            f"Expected {formatted!r} in summary.\nActual: {verdict.summary!r}"
        )


# ---------------------------------------------------------------------------
# IT-22 — Performance
# ---------------------------------------------------------------------------

class TestPerformance:
    """Smoke-test that the sync agent completes in a reasonable time."""

    def test_run_audit_completes_under_10_seconds(self, mocker) -> None:
        """IT-22: full audit with mocked LLM must finish in under 10 seconds."""
        _build_mock_llm_client(mocker, make_clean_violation_llm_responses())
        start = time.monotonic()
        run_audit(make_clean_violation_case())
        elapsed = time.monotonic() - start
        assert elapsed < 10.0, (
            f"run_audit took {elapsed:.2f}s — exceeded 10-second budget"
        )
