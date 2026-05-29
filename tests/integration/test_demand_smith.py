"""Integration tests for DemandSmith — end-to-end pipeline.

Runs ComplianceAuditor → RecoveryOracle → DemandSmith for all three demo fixtures.

LLM mocking strategy:
  - ComplianceAuditor: patch agents.compliance_auditor.rules.get_llm_client
    (same pattern as test_compliance_auditor.py and test_recovery_oracle.py)
  - RecoveryOracle: _agent.override(model=TestModel(...))
  - DemandSmith: patch agents.demand_smith.demand_smith.get_llm_client with
    FakeListChatModel — this exercises the full NARRATIVE_PROMPT | llm | .content
    chain, not a bypass of _generate_narrative.

All dates are fully determined: explicit filing_date passed to draft_letter().
No test calls date.today().

Test inventory (20 tests)
--------------------------
Group A — clean_violation: firm tone, 6 violations, R007 supporting authority (7)
  DS-I-01  letter.tone == "firm"
  DS-I-02  letter.cited_rules contains all 6 violation IDs
  DS-I-03  letter.cited_rules[0] == "R001" (severity-sorted, R001 leads)
  DS-I-04  85 FR 29638 in letter.supporting_authorities_cited
  DS-I-05  letter.response_deadline == filing_date + 30
  DS-I-06  letter.demand_amount_usd == 3920.00
  DS-I-07  R006 (cannot_evaluate) absent from letter.letter_text

Group B — valid_charge: zero violations → ValueError (2)
  DS-I-08  draft_letter raises ValueError for no-violation verdict
  DS-I-09  ValueError message mentions "no confirmed violations"

Group C — borderline: factual tone, 1 violation (R005) (5)
  DS-I-10  letter.tone == "factual"
  DS-I-11  letter.cited_rules == ["R005"]
  DS-I-12  letter.demand_amount_usd == 1800.00
  DS-I-13  cannot_evaluate rules (R004, R006, R007) absent from letter.letter_text
  DS-I-14  supporting_authorities_cited is empty (R005 has no supporting authority)

Group D — cross-fixture invariants (6)
  DS-I-15  DemandLetter.word_count == len(letter_text.split()) for all lettered fixtures
  DS-I-16  DemandLetter.filing_date == explicitly passed date for all fixtures
  DS-I-17  DemandLetter.response_deadline == filing_date + 30 for all fixtures
  DS-I-18  letter_text contains verbatim carrier name for all fixtures
  DS-I-19  Determinism: same inputs + same filing_date + same FakeListChatModel = identical letter
  DS-I-20  letter_text starts with date string, not log noise, for all fixtures
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from pydantic_ai.models.test import TestModel

from agents.compliance_auditor.compliance_auditor import run_audit
from agents.demand_smith.demand_smith import draft_letter
from agents.recovery_oracle import score_recovery
from agents.recovery_oracle.recovery_oracle import _agent
from data.fixtures.borderline_case import (
    CARRIER_NAME as BORDERLINE_CARRIER,
    CHARGE_AMOUNT_USD as BORDERLINE_AMOUNT,
    make_borderline_case,
    make_borderline_llm_responses,
)
from data.fixtures.clean_violation_case import (
    CARRIER_NAME as CLEAN_CARRIER,
    CHARGE_AMOUNT_USD as CLEAN_AMOUNT,
    make_clean_violation_case,
    make_clean_violation_llm_responses,
)
from data.fixtures.valid_charge_case import (
    make_valid_charge_case,
    make_valid_charge_llm_responses,
)
from data.schemas.case import DisputeCase
from data.schemas.letter import DemandLetter
from data.schemas.score import RecoveryScore
from data.schemas.verdict import ComplianceVerdict

# ---------------------------------------------------------------------------
# Fixture filing dates — explicit, never date.today()
# ---------------------------------------------------------------------------

_CLEAN_FILING = date(2026, 6, 26)        # 1 day after clean_violation invoice_date
_BORDERLINE_FILING = date(2026, 6, 1)    # within 30-day window from borderline invoice_date
_VALID_FILING = date(2026, 4, 1)         # within 30-day window from valid_charge invoice_date

# ---------------------------------------------------------------------------
# RecoveryOracle TestModel responses (copied from test_recovery_oracle.py)
# ---------------------------------------------------------------------------

_CLEAN_RO_ARGS: dict[str, Any] = {
    "case_id": "DEMO-CVF-2026-0001",
    "recovery_probability": 0.87,
    "recommended_action": "auto_file",
    "confidence": 0.90,
    "reasoning": (
        "Six independent FMC Part 541 violations including decisive R001 and R007. "
        "Recovery probability is high."
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
    "recovery_probability": 0.05,
    "recommended_action": "write_off",
    "confidence": 0.88,
    "reasoning": "No violations found. Filing would have no regulatory basis.",
    "key_factors": [
        "Negative: overall_strength = no_merit",
        "Negative: zero violations across all 11 evaluated rules",
        "Positive: R007 cannot_evaluate — gap, not exoneration",
    ],
}

_BORDERLINE_RO_ARGS: dict[str, Any] = {
    "case_id": "DEMO-BDL-2026-0003",
    "recovery_probability": 0.48,
    "recommended_action": "human_review",
    "confidence": 0.76,
    "reasoning": (
        "One R005 gate-timestamp violation found (18-minute discrepancy), but three "
        "rules could not be evaluated due to evidence gaps."
    ),
    "key_factors": [
        "R005 gate-timestamp discrepancy (conf=1.0)",
        "Negative: R004 cannot_evaluate — tariff free-time hours missing",
        "Negative: R007 cannot_evaluate — weather evidence inconclusive",
        "Negative: R006 cannot_evaluate — no appointment records",
        "Positive: invoice amount $1,800 above write-off threshold",
    ],
}

# ---------------------------------------------------------------------------
# Shared narrative for FakeListChatModel
# ---------------------------------------------------------------------------

_CLEAN_NARRATIVE = (
    "Empire Freight Lines has issued a demurrage invoice against Pacifica Apparel Inc. "
    "for container MEDU9871234 that is defective under 46 CFR Part 541 on multiple "
    "independent grounds. The invoice was issued forty-seven calendar days after the "
    "charge was incurred, well beyond the regulatory thirty-day issuance window. "
    "The billed period further coincided with a documented ILWU Local 13 work stoppage "
    "during which container retrieval was physically impossible. Additionally, the invoice "
    "understates contractually available free time and bills demurrage on an hourly basis "
    "rather than per calendar day as required."
)

_BORDERLINE_NARRATIVE = (
    "Coastal Carrier Group has issued a demurrage invoice against Heartland Imports LLC "
    "for container CMAU7723445 containing a gate-timestamp discrepancy that constitutes "
    "a violation of 46 CFR Part 541. The recorded gate-in timestamp is eighteen minutes "
    "before free time commencement, exceeding the accepted fifteen-minute tolerance for "
    "terminal operating system reconciliation. This discrepancy undermines the evidentiary "
    "basis for the specific charge dates stated on the invoice."
)

# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

_DEFAULT_LLM: dict[str, Any] = {
    "violated": False, "confidence": 0.85, "reasoning": "Default mock: no issue."
}


def _mock_ca_llm(mocker: Any, per_purpose: dict[str, dict[str, Any]]) -> None:
    def _side_effect(*, purpose: str, **_kw: Any) -> MagicMock:
        matched = _DEFAULT_LLM
        for key, resp in per_purpose.items():
            if key in purpose:
                matched = resp
                break
        mc = MagicMock()
        ch = MagicMock()
        ch.invoke.return_value = MagicMock(
            violated=matched.get("violated", False),
            confidence=matched.get("confidence", 0.85),
            reasoning=matched.get("reasoning", "Mock."),
        )
        mc.with_structured_output.return_value = ch
        return mc

    mocker.patch("agents.compliance_auditor.rules.get_llm_client", side_effect=_side_effect)


def _run_pipeline(
    mocker: Any,
    case: DisputeCase,
    ca_responses: dict[str, dict[str, Any]],
    ro_output_args: dict[str, Any],
) -> tuple[ComplianceVerdict, RecoveryScore]:
    _mock_ca_llm(mocker, ca_responses)
    verdict = run_audit(case)
    with _agent.override(model=TestModel(custom_output_args=ro_output_args)):
        score = score_recovery(verdict, case)
    return verdict, score


def _run_full_pipeline(
    mocker: Any,
    case: DisputeCase,
    ca_responses: dict[str, dict[str, Any]],
    ro_output_args: dict[str, Any],
    narrative: str,
    filing_date: date,
) -> DemandLetter:
    verdict, score = _run_pipeline(mocker, case, ca_responses, ro_output_args)
    fake_llm = FakeListChatModel(responses=[narrative])
    with patch("agents.demand_smith.demand_smith.get_llm_client", return_value=fake_llm):
        return draft_letter(verdict, score, case, filing_date=filing_date)


# ---------------------------------------------------------------------------
# Session-scoped pipeline results (run once, reused by all tests in the group)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def clean_letter(tmp_path_factory: Any) -> DemandLetter:
    """Run the full clean_violation pipeline once for the module."""
    import logging
    logging.disable(logging.CRITICAL)
    from unittest.mock import MagicMock, patch as _patch

    case = make_clean_violation_case()
    ca_resp = make_clean_violation_llm_responses()

    def _ca(*, purpose: str, **_kw: Any) -> MagicMock:
        matched = _DEFAULT_LLM
        for k, r in ca_resp.items():
            if k in purpose:
                matched = r; break
        mc = MagicMock(); ch = MagicMock()
        ch.invoke.return_value = MagicMock(
            violated=matched.get("violated", False),
            confidence=matched.get("confidence", 0.85),
            reasoning=matched.get("reasoning", "Mock."),
        )
        mc.with_structured_output.return_value = ch
        return mc

    with _patch("agents.compliance_auditor.rules.get_llm_client", side_effect=_ca):
        verdict = run_audit(case)
    with _agent.override(model=TestModel(custom_output_args=_CLEAN_RO_ARGS)):
        score = score_recovery(verdict, case)
    fake_llm = FakeListChatModel(responses=[_CLEAN_NARRATIVE])
    with _patch("agents.demand_smith.demand_smith.get_llm_client", return_value=fake_llm):
        return draft_letter(verdict, score, case, filing_date=_CLEAN_FILING)


@pytest.fixture(scope="module")
def borderline_letter(tmp_path_factory: Any) -> DemandLetter:
    """Run the full borderline pipeline once for the module."""
    import logging
    logging.disable(logging.CRITICAL)
    from unittest.mock import MagicMock, patch as _patch

    case = make_borderline_case()
    ca_resp = make_borderline_llm_responses()

    def _ca(*, purpose: str, **_kw: Any) -> MagicMock:
        matched = _DEFAULT_LLM
        for k, r in ca_resp.items():
            if k in purpose:
                matched = r; break
        mc = MagicMock(); ch = MagicMock()
        ch.invoke.return_value = MagicMock(
            violated=matched.get("violated", False),
            confidence=matched.get("confidence", 0.85),
            reasoning=matched.get("reasoning", "Mock."),
        )
        mc.with_structured_output.return_value = ch
        return mc

    with _patch("agents.compliance_auditor.rules.get_llm_client", side_effect=_ca):
        verdict = run_audit(case)
    with _agent.override(model=TestModel(custom_output_args=_BORDERLINE_RO_ARGS)):
        score = score_recovery(verdict, case)
    fake_llm = FakeListChatModel(responses=[_BORDERLINE_NARRATIVE])
    with _patch("agents.demand_smith.demand_smith.get_llm_client", return_value=fake_llm):
        return draft_letter(verdict, score, case, filing_date=_BORDERLINE_FILING)


# ---------------------------------------------------------------------------
# Group A — clean_violation fixture
# ---------------------------------------------------------------------------


class TestCleanViolationLetter:
    def test_tone_is_firm(self, clean_letter: DemandLetter):  # DS-I-01
        assert clean_letter.tone == "firm"

    def test_cited_rules_contains_all_six(self, clean_letter: DemandLetter):  # DS-I-02
        expected = {"R001", "R002", "R004", "R007", "R011", "R012"}
        assert expected.issubset(set(clean_letter.cited_rules))

    def test_r001_leads_severity_order(self, clean_letter: DemandLetter):  # DS-I-03
        assert clean_letter.cited_rules[0] == "R001"

    def test_85fr29638_in_supporting_authorities(self, clean_letter: DemandLetter):  # DS-I-04
        assert any("85 FR 29638" in sa for sa in clean_letter.supporting_authorities_cited)

    def test_response_deadline_filing_plus_30(self, clean_letter: DemandLetter):  # DS-I-05
        assert clean_letter.response_deadline == _CLEAN_FILING + timedelta(days=30)

    def test_demand_amount_from_invoice(self, clean_letter: DemandLetter):  # DS-I-06
        assert clean_letter.demand_amount_usd == CLEAN_AMOUNT

    def test_r006_cannot_evaluate_absent(self, clean_letter: DemandLetter):  # DS-I-07
        # R006 is cannot_evaluate; must not appear in the letter body
        assert "R006" not in clean_letter.letter_text
        assert "Appointment compliance" not in clean_letter.letter_text


# ---------------------------------------------------------------------------
# Group B — valid_charge: zero violations → ValueError
# ---------------------------------------------------------------------------


class TestValidChargeLetter:
    def test_zero_violations_raises_value_error(self, mocker: Any):  # DS-I-08
        case = make_valid_charge_case()
        ca_resp = make_valid_charge_llm_responses()
        _mock_ca_llm(mocker, ca_resp)
        verdict = run_audit(case)
        with _agent.override(model=TestModel(custom_output_args=_VALID_RO_ARGS)):
            score = score_recovery(verdict, case)
        assert len(verdict.violations) == 0
        with pytest.raises(ValueError):
            draft_letter(verdict, score, case, filing_date=_VALID_FILING)

    def test_value_error_message_describes_cause(self, mocker: Any):  # DS-I-09
        case = make_valid_charge_case()
        ca_resp = make_valid_charge_llm_responses()
        _mock_ca_llm(mocker, ca_resp)
        verdict = run_audit(case)
        with _agent.override(model=TestModel(custom_output_args=_VALID_RO_ARGS)):
            score = score_recovery(verdict, case)
        with pytest.raises(ValueError, match="no confirmed violations"):
            draft_letter(verdict, score, case, filing_date=_VALID_FILING)


# ---------------------------------------------------------------------------
# Group C — borderline fixture
# ---------------------------------------------------------------------------


class TestBorderlineLetter:
    def test_tone_is_factual(self, borderline_letter: DemandLetter):  # DS-I-10
        assert borderline_letter.tone == "factual"

    def test_cited_rules_is_r005_only(self, borderline_letter: DemandLetter):  # DS-I-11
        assert borderline_letter.cited_rules == ["R005"]

    def test_demand_amount_from_invoice(self, borderline_letter: DemandLetter):  # DS-I-12
        assert borderline_letter.demand_amount_usd == BORDERLINE_AMOUNT

    def test_cannot_evaluate_rules_absent(self, borderline_letter: DemandLetter):  # DS-I-13
        for rule_id in ("R004", "R006", "R007"):
            assert rule_id not in borderline_letter.letter_text

    def test_no_supporting_authorities_for_r005(
        self, borderline_letter: DemandLetter
    ):  # DS-I-14
        assert borderline_letter.supporting_authorities_cited == []


# ---------------------------------------------------------------------------
# Group D — cross-fixture invariants
# ---------------------------------------------------------------------------


class TestCrossFixtureInvariants:
    @pytest.mark.parametrize("fixture_name,filing_date", [
        ("clean", _CLEAN_FILING),
        ("borderline", _BORDERLINE_FILING),
    ])
    def test_word_count_matches_letter_text(
        self,
        fixture_name: str,
        filing_date: date,
        clean_letter: DemandLetter,
        borderline_letter: DemandLetter,
    ):  # DS-I-15
        letter = clean_letter if fixture_name == "clean" else borderline_letter
        assert letter.word_count == len(letter.letter_text.split())

    @pytest.mark.parametrize("fixture_name,filing_date", [
        ("clean", _CLEAN_FILING),
        ("borderline", _BORDERLINE_FILING),
    ])
    def test_filing_date_matches_explicit_param(
        self,
        fixture_name: str,
        filing_date: date,
        clean_letter: DemandLetter,
        borderline_letter: DemandLetter,
    ):  # DS-I-16
        letter = clean_letter if fixture_name == "clean" else borderline_letter
        assert letter.filing_date == filing_date

    @pytest.mark.parametrize("fixture_name,filing_date", [
        ("clean", _CLEAN_FILING),
        ("borderline", _BORDERLINE_FILING),
    ])
    def test_deadline_is_filing_plus_30(
        self,
        fixture_name: str,
        filing_date: date,
        clean_letter: DemandLetter,
        borderline_letter: DemandLetter,
    ):  # DS-I-17
        letter = clean_letter if fixture_name == "clean" else borderline_letter
        assert letter.response_deadline == filing_date + timedelta(days=30)

    @pytest.mark.parametrize("fixture_name,carrier_name", [
        ("clean", CLEAN_CARRIER),
        ("borderline", BORDERLINE_CARRIER),
    ])
    def test_letter_contains_carrier_name(
        self,
        fixture_name: str,
        carrier_name: str,
        clean_letter: DemandLetter,
        borderline_letter: DemandLetter,
    ):  # DS-I-18
        letter = clean_letter if fixture_name == "clean" else borderline_letter
        assert carrier_name in letter.letter_text

    def test_determinism_clean_fixture(self, mocker: Any):  # DS-I-19
        """Same inputs + same filing_date + same FakeListChatModel = identical letter."""
        import logging
        logging.disable(logging.CRITICAL)

        def _make_letter() -> DemandLetter:
            return _run_full_pipeline(
                mocker,
                make_clean_violation_case(),
                make_clean_violation_llm_responses(),
                _CLEAN_RO_ARGS,
                _CLEAN_NARRATIVE,
                _CLEAN_FILING,
            )

        letter_a = _make_letter()
        letter_b = _make_letter()
        assert letter_a.letter_text == letter_b.letter_text
        assert letter_a.word_count == letter_b.word_count

    @pytest.mark.parametrize("fixture_name,filing_date,carrier", [
        ("clean", _CLEAN_FILING, CLEAN_CARRIER),
        ("borderline", _BORDERLINE_FILING, BORDERLINE_CARRIER),
    ])
    def test_letter_starts_with_date_not_log_noise(
        self,
        fixture_name: str,
        filing_date: date,
        carrier: str,
        clean_letter: DemandLetter,
        borderline_letter: DemandLetter,
    ):  # DS-I-20
        letter = clean_letter if fixture_name == "clean" else borderline_letter
        expected_date_str = filing_date.strftime("%B %d, %Y")
        assert letter.letter_text.startswith(expected_date_str)
