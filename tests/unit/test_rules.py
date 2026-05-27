"""Unit tests for deterministic rule functions (Section 1 of rules.py).

Phase B coverage: R001, R009, R010, R011, R012.
Phase C coverage: R002, R003, R004, R005, R008 (mixed rules).
Phase D coverage: R006, R007 (LLM-primary rules).

Test matrix per rule (canonical scenario names):
  clear_violation      — unambiguous violation, confidence == 1.0
  clear_pass           — unambiguous compliance, confidence == 1.0
  boundary             — exactly-at-the-limit case, confidence == 0.95 (R001)
                         or within-tolerance case (R009)
  cannot_evaluate      — prerequisite evidence missing, violated is None
                         (N/A variants used where cannot_evaluate is impossible)

R011 adds a fifth scenario:
  not_applicable_detention — charge_type != 'demurrage', returns clean pass

LLM-assisted rules (R006, R007) and mixed rules (R002–R005, R008) are
tested in Phase C/D with mocked LLM clients.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from agents.compliance_auditor.rules import (
    evaluate_R001,
    evaluate_R002,
    evaluate_R003,
    evaluate_R004,
    evaluate_R005,
    evaluate_R006,
    evaluate_R007,
    evaluate_R008,
    evaluate_R009,
    evaluate_R010,
    evaluate_R011,
    evaluate_R012,
    _compute_dispute_status,
)
from data.schemas.verdict import RuleResult
from tests.unit.conftest import make_case


# ===========================================================================
# R001 — Invoicing window
# ===========================================================================

@pytest.mark.parametrize(
    "delta_days, exp_violated, exp_confidence",
    [
        (47,  True,  1.0),   # clear_violation: 47 days after charge
        (15,  False, 1.0),   # clear_pass: 15 days after charge
        (30,  False, 0.95),  # boundary: exactly 30 days — compliant, reduced confidence
        (0,   False, 1.0),   # cannot_evaluate_na: same-day invoice (R001 has no missing-data path)
        (31,  True,  1.0),   # day_31: one day over the limit, clear violation
    ],
    ids=["clear_violation", "clear_pass", "boundary", "cannot_evaluate_na", "day_31"],
)
def test_R001_invoicing_window(delta_days: int, exp_violated: bool, exp_confidence: float) -> None:
    """R001 correctly flags invoices based on days elapsed since charge."""
    base_date = date(2024, 6, 1)
    case = make_case(
        invoice_overrides=dict(
            charge_incurred_date=base_date,
            invoice_date=base_date + timedelta(days=delta_days),
        )
    )
    result = evaluate_R001(case)

    assert isinstance(result, RuleResult)
    assert result.rule_id == "R001"
    assert result.violated is exp_violated, (
        f"delta={delta_days}d: expected violated={exp_violated}, got {result.violated}"
    )
    assert result.confidence == exp_confidence, (
        f"delta={delta_days}d: expected confidence={exp_confidence}, got {result.confidence}"
    )
    assert "invoice.invoice_date" in result.evidence_refs
    assert "invoice.charge_incurred_date" in result.evidence_refs
    assert result.citation  # non-empty citation drawn from §541.7(a)
    assert "541.7" in result.citation or "30" in result.citation


def test_R001_boundary_reasoning_note() -> None:
    """R001 boundary result includes a note about the 30-day boundary."""
    case = make_case(invoice_overrides=dict(
        charge_incurred_date=date(2024, 6, 1),
        invoice_date=date(2024, 7, 1),  # exactly 30 days
    ))
    result = evaluate_R001(case)
    assert result.violated is False
    assert result.confidence == 0.95
    assert "boundary" in result.reasoning.lower() or "30" in result.reasoning


# ===========================================================================
# R009 — Charge calculation
# ===========================================================================

@pytest.mark.parametrize(
    "hours, rate, total, exp_violated",
    [
        (8.0,  75.0, 650.00, True),   # clear_violation: 8×75=600 but billed 650
        (8.0,  75.0, 600.00, False),  # clear_pass: exact match
        (8.0,  75.0, 600.01, False),  # boundary: off by exactly $0.01 — within tolerance
        (10.0, 50.0, 501.00, True),   # cannot_evaluate_na: R009 has no missing-data path; use $1 over
    ],
    ids=["clear_violation", "clear_pass", "boundary", "cannot_evaluate_na"],
)
def test_R009_charge_calculation(
    hours: float, rate: float, total: float, exp_violated: bool
) -> None:
    """R009 flags arithmetic inconsistencies with $0.01 tolerance."""
    case = make_case(invoice_overrides=dict(
        hours_billed=hours,
        hourly_rate_usd=rate,
        total_amount_usd=total,
    ))
    result = evaluate_R009(case)

    assert result.rule_id == "R009"
    assert result.violated is exp_violated, (
        f"{hours}h×${rate}=${hours*rate:.2f} vs billed ${total}: "
        f"expected violated={exp_violated}, got {result.violated}"
    )
    assert result.confidence == 1.0
    assert "invoice.hours_billed" in result.evidence_refs
    assert "invoice.hourly_rate_usd" in result.evidence_refs
    assert "invoice.total_amount_usd" in result.evidence_refs


def test_R009_just_over_tolerance_is_violation() -> None:
    """R009: $0.011 delta is a violation (just outside $0.01 tolerance)."""
    case = make_case(invoice_overrides=dict(
        hours_billed=8.0,
        hourly_rate_usd=75.0,
        total_amount_usd=600.011,  # 0.011 over
    ))
    result = evaluate_R009(case)
    assert result.violated is True


def test_R009_reasoning_contains_computed_expected() -> None:
    """R009 reasoning string shows the expected vs actual amount."""
    case = make_case(invoice_overrides=dict(
        hours_billed=10.0, hourly_rate_usd=50.0, total_amount_usd=600.00,
    ))
    result = evaluate_R009(case)
    assert "500" in result.reasoning  # 10×50=500
    assert "600" in result.reasoning  # billed 600


# ===========================================================================
# R010 — Free time consumption
# ===========================================================================

@pytest.mark.parametrize(
    "charge_incurred_date, free_time_end, exp_violated",
    [
        # clear_violation: billing started 2 days BEFORE free time ended
        (
            date(2024, 5, 30),
            datetime(2024, 6, 1, 8, 0, tzinfo=timezone.utc),
            True,
        ),
        # clear_pass: billing started 2 days AFTER free time ended
        (
            date(2024, 6, 3),
            datetime(2024, 6, 1, 8, 0, tzinfo=timezone.utc),
            False,
        ),
        # boundary: billing started on the same calendar day free time ended — compliant
        (
            date(2024, 6, 1),
            datetime(2024, 6, 1, 23, 59, tzinfo=timezone.utc),
            False,
        ),
        # cannot_evaluate: free_time_end is None
        (
            date(2024, 6, 3),
            None,  # signals cannot_evaluate in test body
            None,
        ),
    ],
    ids=["clear_violation", "clear_pass", "boundary", "cannot_evaluate"],
)
def test_R010_free_time_consumption(
    charge_incurred_date: date,
    free_time_end: datetime | None,
    exp_violated: bool | None,
) -> None:
    """R010 checks billing only begins after free time is fully consumed."""
    inv_overrides: dict = dict(charge_incurred_date=charge_incurred_date)
    if free_time_end is not None:
        inv_overrides["free_time_end"] = free_time_end
        inv_overrides["free_time_start"] = datetime(2024, 5, 29, 8, 0, tzinfo=timezone.utc)
    else:
        inv_overrides["free_time_end"] = None
        inv_overrides["free_time_start"] = None

    case = make_case(invoice_overrides=inv_overrides)
    result = evaluate_R010(case)

    assert result.rule_id == "R010"
    assert result.violated is exp_violated, (
        f"charge={charge_incurred_date} free_end={free_time_end}: "
        f"expected {exp_violated}, got {result.violated}"
    )
    if exp_violated is None:
        # cannot_evaluate: check missing_evidence is populated
        assert result.missing_evidence is not None
        assert len(result.missing_evidence) >= 1
        assert any(
            "free_time_end" in item.field_path
            for item in result.missing_evidence
        )
    else:
        assert result.confidence == 1.0


def test_R010_cannot_evaluate_has_retrieval_hint() -> None:
    """R010 cannot_evaluate result includes a retrieval source hint."""
    case = make_case(invoice_overrides=dict(free_time_end=None, free_time_start=None))
    result = evaluate_R010(case)
    assert result.violated is None
    assert result.missing_evidence is not None
    sources = [src for item in result.missing_evidence for src in (item.can_be_fetched_from or [])]
    assert sources, "Missing evidence items should include at least one retrieval source"


# ===========================================================================
# R011 — Per-day vs per-hour rule application
# ===========================================================================

@pytest.mark.parametrize(
    "charge_type, hours_billed, exp_violated, exp_confidence, scenario",
    [
        # clear_violation: demurrage billed in fractional days (per-hour)
        ("demurrage", 25.0,  True,  1.0, "clear_violation"),
        # clear_pass: demurrage billed in clean 3-day units
        ("demurrage", 72.0,  False, 1.0, "clear_pass"),
        # boundary: exactly 1 day (24h) — clean multiple, compliant
        ("demurrage", 24.0,  False, 1.0, "boundary"),
        # cannot_evaluate_na: R011 has no missing-data path; use 48h (2 days) clean
        ("demurrage", 48.0,  False, 1.0, "cannot_evaluate_na"),
        # not_applicable_detention: charge_type is detention, rule doesn't apply
        ("detention", 8.5,   False, 1.0, "not_applicable_detention"),
    ],
    ids=["clear_violation", "clear_pass", "boundary", "cannot_evaluate_na", "not_applicable_detention"],
)
def test_R011_per_day_vs_per_hour(
    charge_type: str,
    hours_billed: float,
    exp_violated: bool,
    exp_confidence: float,
    scenario: str,
) -> None:
    """R011 validates per-day demurrage billing and ignores non-demurrage charges."""
    case = make_case(invoice_overrides=dict(
        charge_type=charge_type,
        hours_billed=hours_billed,
        # adjust total to keep R009 clean
        total_amount_usd=hours_billed * 75.0,
    ))
    result = evaluate_R011(case)

    assert result.rule_id == "R011"
    assert result.violated is exp_violated, (
        f"[{scenario}] charge_type={charge_type!r} hours={hours_billed}: "
        f"expected violated={exp_violated}, got {result.violated}"
    )
    assert result.confidence == exp_confidence


def test_R011_not_applicable_reasoning_string() -> None:
    """R011 not-applicable result contains 'Not applicable' in reasoning."""
    case = make_case(invoice_overrides=dict(charge_type="detention", hours_billed=8.0, total_amount_usd=600.0))
    result = evaluate_R011(case)
    assert result.violated is False
    assert result.confidence == 1.0
    assert "Not applicable" in result.reasoning
    assert "detention" in result.reasoning


def test_R011_not_applicable_evidence_ref_is_charge_type() -> None:
    """R011 not-applicable result references invoice.charge_type."""
    case = make_case(invoice_overrides=dict(charge_type="tonu", hours_billed=4.0, total_amount_usd=300.0))
    result = evaluate_R011(case)
    assert "invoice.charge_type" in result.evidence_refs


@pytest.mark.parametrize("charge_type", ["tonu", "layover", "lumper", "other"])
def test_R011_all_non_demurrage_types_are_not_applicable(charge_type: str) -> None:
    """R011 returns not-applicable for every non-demurrage charge type."""
    case = make_case(invoice_overrides=dict(
        charge_type=charge_type, hours_billed=5.0, total_amount_usd=375.0,
    ))
    result = evaluate_R011(case)
    assert result.violated is False
    assert result.confidence == 1.0


# ===========================================================================
# R012 — Dispute mechanism disclosure
# ===========================================================================

@pytest.mark.parametrize(
    "basis_for_charge, bol_terms, exp_violated",
    [
        # clear_violation: text present but no URL or contact marker
        ("Standard detention charge per tariff.", "Shipper: ACME. Carrier: Atlas.", True),
        # clear_pass: URL present in basis_for_charge
        ("Contact disputes@atlasshipping.com or https://atlasshipping.com/disputes", None, False),
        # boundary: phone number present — contact marker detected
        ("Call 555-123-4567 to dispute this charge.", None, False),
        # cannot_evaluate: both fields None
        (None, None, None),
    ],
    ids=["clear_violation", "clear_pass", "boundary", "cannot_evaluate"],
)
def test_R012_dispute_mechanism_disclosure(
    basis_for_charge: str | None,
    bol_terms: str | None,
    exp_violated: bool | None,
) -> None:
    """R012 detects presence or absence of §541.6(d) dispute disclosures."""
    case = make_case(
        invoice_overrides=dict(basis_for_charge=basis_for_charge),
        evidence_overrides=dict(bol_terms=bol_terms),
    )
    result = evaluate_R012(case)

    assert result.rule_id == "R012"
    assert result.violated is exp_violated, (
        f"basis={basis_for_charge!r} bol={bol_terms!r}: "
        f"expected violated={exp_violated}, got {result.violated}"
    )
    if exp_violated is None:
        assert result.missing_evidence is not None
        assert len(result.missing_evidence) == 2
        paths = {item.field_path for item in result.missing_evidence}
        assert "invoice.basis_for_charge" in paths
        assert "evidence.bol_terms" in paths
    else:
        assert result.confidence == 1.0


def test_R012_url_in_bol_terms_is_sufficient() -> None:
    """R012 passes when URL is in bol_terms even if basis_for_charge is None."""
    case = make_case(
        invoice_overrides=dict(basis_for_charge=None),
        evidence_overrides=dict(bol_terms="Disputes: visit https://carrier.com/disputes"),
    )
    result = evaluate_R012(case)
    assert result.violated is False
    assert "evidence.bol_terms" in result.evidence_refs


def test_R012_cannot_evaluate_lists_retrieval_sources() -> None:
    """R012 cannot_evaluate items include at least one can_be_fetched_from source."""
    case = make_case(
        invoice_overrides=dict(basis_for_charge=None),
        evidence_overrides=dict(bol_terms=None),
    )
    result = evaluate_R012(case)
    assert result.violated is None
    assert result.missing_evidence is not None
    for item in result.missing_evidence:
        assert item.can_be_fetched_from, f"{item.field_path} has no retrieval hint"


def test_R012_violation_logs_warning(caplog) -> None:
    """R012 emits a WARNING when a potential violation is detected."""
    import logging
    case = make_case(
        invoice_overrides=dict(basis_for_charge="Freight charge."),
        evidence_overrides=dict(bol_terms=None),
    )
    with caplog.at_level(logging.WARNING, logger="agents.compliance_auditor.rules"):
        result = evaluate_R012(case)
    assert result.violated is True
    assert any("R012" in r.message or "violation" in r.message.lower() for r in caplog.records)


# ===========================================================================
# Cross-rule: citation field contract
# ===========================================================================

@pytest.mark.parametrize("evaluate_fn, rule_id", [
    (evaluate_R001, "R001"),
    (evaluate_R009, "R009"),
    (evaluate_R010, "R010"),
    (evaluate_R011, "R011"),
    (evaluate_R012, "R012"),
])
def test_citation_field_matches_registry(evaluate_fn, rule_id) -> None:
    """Every rule's citation field equals get_citation(rule_id).verbatim_text."""
    from agents.compliance_auditor.citations import get_citation

    case = make_case()
    result = evaluate_fn(case)
    assert result.citation == get_citation(rule_id).verbatim_text, (
        f"{rule_id}: citation field diverges from citations registry"
    )


@pytest.mark.parametrize("evaluate_fn", [
    evaluate_R001, evaluate_R009, evaluate_R010, evaluate_R011, evaluate_R012,
])
def test_result_is_ruleresult_instance(evaluate_fn) -> None:
    """Every deterministic rule returns a RuleResult instance."""
    case = make_case()
    result = evaluate_fn(case)
    assert isinstance(result, RuleResult)


@pytest.mark.parametrize("evaluate_fn", [
    evaluate_R001, evaluate_R009, evaluate_R011, evaluate_R012,
])
def test_confident_result_has_no_missing_evidence(evaluate_fn) -> None:
    """Rules that return a definitive violated=True/False have no missing_evidence."""
    case = make_case()
    result = evaluate_fn(case)
    if result.violated is not None:
        assert result.missing_evidence is None or result.missing_evidence == [], (
            f"{result.rule_id}: violated={result.violated} but missing_evidence is set"
        )


# ===========================================================================
# PHASE C — Mixed rules (R002, R003, R004, R005, R008)
# ===========================================================================

# ---------------------------------------------------------------------------
# Shared LLM mock helper
# ---------------------------------------------------------------------------

def _mock_llm(mocker, target_module_path: str,
              *, violated: bool | None, confidence: float, reasoning: str):
    """Patch get_llm_client in a rules module and return the mock client."""
    mock_client = mocker.MagicMock()
    mock_chain = mocker.MagicMock()
    mock_chain.invoke.return_value = mocker.MagicMock(
        violated=violated, confidence=confidence, reasoning=reasoning,
    )
    mock_client.with_structured_output.return_value = mock_chain
    mocker.patch(target_module_path, return_value=mock_client)
    return mock_client


_RULES_LLM_PATH = "agents.compliance_auditor.rules.get_llm_client"


# ===========================================================================
# R008 — Dispute window status (no LLM; status reporter)
# ===========================================================================

class TestR008DisputeWindowStatus:
    """R008 always returns violated=False with a STATUS token in reasoning."""

    def test_r008_violated_is_always_false(self) -> None:
        """R008 never reports a violation."""
        case = make_case()
        result = evaluate_R008(case)
        assert result.violated is False
        assert result.confidence == 1.0
        assert result.rule_id == "R008"

    def test_r008_status_token_in_reasoning(self) -> None:
        """R008 reasoning contains a STATUS: prefix for machine parsing."""
        case = make_case()
        result = evaluate_R008(case)
        assert "STATUS:" in result.reasoning

    def test_r008_window_open_no_dispute(self) -> None:
        """Recent invoice with no dispute_notice_date → window_open_dispute_not_filed."""
        from datetime import date
        case = make_case(invoice_overrides=dict(
            charge_incurred_date=date.today(),
            invoice_date=date.today(),
        ), evidence_overrides=dict(dispute_notice_date=None))
        status = _compute_dispute_status(case)
        assert status == "window_open_dispute_not_filed"
        result = evaluate_R008(case)
        assert "window_open_dispute_not_filed" in result.reasoning

    def test_r008_window_open_dispute_filed(self) -> None:
        """Recent invoice with dispute_notice_date → window_open_dispute_filed."""
        from datetime import date
        today = date.today()
        case = make_case(invoice_overrides=dict(
            charge_incurred_date=today,
            invoice_date=today,
        ), evidence_overrides=dict(dispute_notice_date=today))
        status = _compute_dispute_status(case)
        assert status == "window_open_dispute_filed"
        result = evaluate_R008(case)
        assert "window_open_dispute_filed" in result.reasoning

    def test_r008_window_expired_no_dispute(self) -> None:
        """Old invoice with no dispute_notice_date → window_expired_no_dispute."""
        from datetime import date, timedelta
        old_date = date.today() - timedelta(days=60)
        case = make_case(invoice_overrides=dict(
            charge_incurred_date=old_date - timedelta(days=10),
            invoice_date=old_date,
        ), evidence_overrides=dict(dispute_notice_date=None))
        status = _compute_dispute_status(case)
        assert status == "window_expired_no_dispute"
        result = evaluate_R008(case)
        assert "window_expired_no_dispute" in result.reasoning

    def test_r008_window_expired_dispute_filed(self) -> None:
        """Old invoice with dispute_notice_date → window_expired_dispute_filed."""
        from datetime import date, timedelta
        old_date = date.today() - timedelta(days=60)
        dispute_date = old_date + timedelta(days=10)
        case = make_case(invoice_overrides=dict(
            charge_incurred_date=old_date - timedelta(days=10),
            invoice_date=old_date,
        ), evidence_overrides=dict(dispute_notice_date=dispute_date))
        status = _compute_dispute_status(case)
        assert status == "window_expired_dispute_filed"
        result = evaluate_R008(case)
        assert "window_expired_dispute_filed" in result.reasoning


# ===========================================================================
# R002 — Required minimum content (mixed)
# ===========================================================================

class TestR002RequiredMinimumContent:
    """R002: deterministic for missing fields; LLM fallback for generic basis."""

    def test_clear_violation_missing_bol_number(self) -> None:
        """R002 clear_violation: bol_number is None → violated=True, confidence=1.0."""
        case = make_case(invoice_overrides=dict(bol_number=None))
        result = evaluate_R002(case)
        assert result.violated is True
        assert result.confidence == 1.0
        assert "invoice.bol_number" in result.reasoning or "bol_number" in result.reasoning

    def test_clear_violation_missing_basis_for_charge(self) -> None:
        """R002 clear_violation: basis_for_charge is None → violated=True, confidence=1.0."""
        case = make_case(invoice_overrides=dict(basis_for_charge=None))
        result = evaluate_R002(case)
        assert result.violated is True
        assert result.confidence == 1.0
        assert result.missing_evidence is not None

    def test_clear_pass_all_fields_present_substantive(self) -> None:
        """R002 clear_pass: all fields present, basis_for_charge substantive → no LLM."""
        case = make_case(invoice_overrides=dict(
            basis_for_charge=(
                "Container ATSU1234567 was held at APMT terminal beyond "
                "the 48-hour free-time period ending 2024-06-01 08:00 UTC. "
                "ACME Imports LLC, as the consignee of BOL BOLT123456, is "
                "responsible for demurrage under the applicable tariff terms."
            )
        ))
        result = evaluate_R002(case)
        assert result.violated is False
        assert result.confidence == 1.0

    @pytest.mark.parametrize("generic_basis", [
        "detention charge",
        "per tariff",
        "standard rate",
        "N/A",
        "short",  # fewer than 10 chars
    ])
    def test_llm_fallback_triggered_for_generic_basis(self, mocker, generic_basis: str) -> None:
        """R002 routes to LLM when basis_for_charge is short or a generic phrase."""
        mock_client = _mock_llm(
            mocker, _RULES_LLM_PATH,
            violated=True, confidence=0.8,
            reasoning="Basis is a generic phrase; does not explain liability.",
        )
        case = make_case(invoice_overrides=dict(basis_for_charge=generic_basis))
        result = evaluate_R002(case)
        mock_client.with_structured_output.assert_called_once()
        assert result.violated is True
        assert 0.6 <= result.confidence <= 0.95

    def test_llm_result_confidence_clamped(self, mocker) -> None:
        """R002 LLM confidence is clamped to [0.6, 0.95] even if LLM returns 0.99."""
        _mock_llm(mocker, _RULES_LLM_PATH,
                  violated=False, confidence=0.99, reasoning="Looks OK.")
        case = make_case(invoice_overrides=dict(basis_for_charge="std"))
        result = evaluate_R002(case)
        assert result.confidence == 0.95  # clamped from 0.99

    def test_no_llm_call_when_field_clearly_missing(self, mocker) -> None:
        """R002 does NOT call LLM when a required field is outright None."""
        mock_client = mocker.patch(_RULES_LLM_PATH)
        case = make_case(invoice_overrides=dict(container_number=None))
        evaluate_R002(case)
        mock_client.assert_not_called()


# ===========================================================================
# R003 — Correct billing party (mixed)
# ===========================================================================

class TestR003CorrectBillingParty:
    """R003: deterministic match against shipper_name/bol_terms; LLM for ambiguous BOL."""

    def test_clear_pass_shipper_name_exact_match(self) -> None:
        """R003 clear_pass: billed_to_party == shipper_name → violated=False, conf=1.0."""
        case = make_case(
            invoice_overrides=dict(billed_to_party="ACME Imports LLC"),
            evidence_overrides=dict(shipper_name="ACME Imports LLC"),
        )
        result = evaluate_R003(case)
        assert result.violated is False
        assert result.confidence == 1.0

    def test_clear_violation_mismatch_no_bol(self) -> None:
        """R003 clear_violation: billed_to_party != shipper_name and no BOL → violated=True."""
        case = make_case(
            invoice_overrides=dict(billed_to_party="Completely Different Corp"),
            evidence_overrides=dict(shipper_name="ACME Imports LLC", bol_terms=None),
        )
        result = evaluate_R003(case)
        assert result.violated is True
        assert result.confidence == 0.95  # boundary confidence per spec

    def test_llm_fallback_triggered_when_bol_present_no_match(self, mocker) -> None:
        """R003 routes to LLM when BOL is present but billed_to_party not found in it."""
        mock_client = _mock_llm(
            mocker, _RULES_LLM_PATH,
            violated=True, confidence=0.78,
            reasoning="Invoiced party is not the contracting party or consignee per BOL.",
        )
        case = make_case(
            invoice_overrides=dict(billed_to_party="Unknown Party Ltd"),
            evidence_overrides=dict(
                shipper_name="ACME Imports LLC",
                bol_terms="Shipper: ACME Imports LLC. Consignee: Global Trade Inc.",
            ),
        )
        result = evaluate_R003(case)
        mock_client.with_structured_output.assert_called_once()
        assert result.violated is True
        assert 0.6 <= result.confidence <= 0.95

    def test_cannot_evaluate_no_shipper_no_bol(self) -> None:
        """R003 cannot_evaluate when both shipper_name and bol_terms are absent."""
        case = make_case(
            evidence_overrides=dict(shipper_name=None, bol_terms=None),
        )
        result = evaluate_R003(case)
        assert result.violated is None
        assert result.missing_evidence is not None
        assert len(result.missing_evidence) >= 1

    def test_no_llm_when_shipper_matches(self, mocker) -> None:
        """R003 does NOT call LLM when shipper_name exactly matches billed_to_party."""
        mock_client = mocker.patch(_RULES_LLM_PATH)
        case = make_case(
            invoice_overrides=dict(billed_to_party="ACME Imports LLC"),
            evidence_overrides=dict(shipper_name="ACME Imports LLC"),
        )
        evaluate_R003(case)
        mock_client.assert_not_called()


# ===========================================================================
# R004 — Free time accuracy (mixed)
# ===========================================================================

class TestR004FreeTimeAccuracy:
    """R004: tariff-based deterministic check; LLM BOL scan as fallback."""

    def test_clear_pass_matches_tariff(self) -> None:
        """R004 clear_pass: invoice free time matches published tariff hours."""
        # conftest default: free_time_start=2024-05-30T08:00, free_time_end=2024-06-01T08:00 → 48h
        # TariffReference.published_free_time_hours=48.0
        case = make_case()
        result = evaluate_R004(case)
        assert result.violated is False
        assert result.confidence == 1.0

    def test_clear_violation_underreported_free_time(self) -> None:
        """R004 clear_violation: invoice claims 24h but tariff says 48h → violated=True."""
        from datetime import datetime, timezone
        case = make_case(invoice_overrides=dict(
            free_time_start=datetime(2024, 5, 31, 8, 0, tzinfo=timezone.utc),
            free_time_end=datetime(2024, 6, 1, 8, 0, tzinfo=timezone.utc),  # 24h only
        ))
        result = evaluate_R004(case)
        assert result.violated is True
        assert result.confidence == 1.0

    def test_llm_fallback_when_no_tariff(self, mocker) -> None:
        """R004 falls back to LLM when carrier_tariff_reference is absent."""
        mock_client = _mock_llm(
            mocker, _RULES_LLM_PATH,
            violated=False, confidence=0.75,
            reasoning="BOL grants 48 hours free time matching invoice.",
        )
        case = make_case(evidence_overrides=dict(
            carrier_tariff_reference=None,
            bol_terms="Free time: 2 days detention-free per BOL terms.",
        ))
        result = evaluate_R004(case)
        mock_client.with_structured_output.assert_called_once()
        assert result.violated is False
        assert 0.6 <= result.confidence <= 0.95

    def test_cannot_evaluate_no_tariff_no_bol(self) -> None:
        """R004 cannot_evaluate when no tariff and no BOL text."""
        case = make_case(evidence_overrides=dict(
            carrier_tariff_reference=None,
            bol_terms=None,
        ))
        result = evaluate_R004(case)
        assert result.violated is None
        assert result.missing_evidence is not None
        paths = {m.field_path for m in result.missing_evidence}
        assert "evidence.carrier_tariff_reference" in paths

    def test_boundary_within_tolerance(self) -> None:
        """R004 boundary: free time is 0.4h short of published — within 0.5h tolerance."""
        from datetime import datetime, timezone
        # published=48h, claimed=47.6h — delta=0.4h < tolerance=0.5h → compliant
        case = make_case(invoice_overrides=dict(
            free_time_start=datetime(2024, 5, 30, 8, 0, tzinfo=timezone.utc),
            free_time_end=datetime(2024, 6, 1, 7, 36, tzinfo=timezone.utc),  # 47h36m = 47.6h
        ))
        result = evaluate_R004(case)
        assert result.violated is False


# ===========================================================================
# R005 — Gate timestamp consistency (mixed)
# ===========================================================================

class TestR005GateTimestampConsistency:
    """R005: deterministic within 15-min tolerance; LLM parses notes as fallback."""

    def test_clear_pass_within_tolerance(self) -> None:
        """R005 clear_pass: gate timestamps within 15 min of invoice timestamps."""
        # Use matching timestamps so both deltas are within tolerance.
        case = make_case(evidence_overrides=dict(
            gate_in_timestamp=datetime(2024, 5, 30, 8, 5, tzinfo=timezone.utc),   # 5 min delta
            gate_out_timestamp=datetime(2024, 6, 1, 8, 5, tzinfo=timezone.utc),   # 5 min delta
        ))
        result = evaluate_R005(case)
        assert result.violated is False
        assert result.confidence == 1.0

    def test_clear_violation_gate_in_discrepancy(self) -> None:
        """R005 clear_violation: gate_in_timestamp is 25 min before free_time_start."""
        from datetime import datetime, timezone
        case = make_case(
            invoice_overrides=dict(
                free_time_start=datetime(2024, 5, 30, 8, 0, tzinfo=timezone.utc),
            ),
            evidence_overrides=dict(
                gate_in_timestamp=datetime(2024, 5, 30, 7, 30, tzinfo=timezone.utc),  # 30 min delta
            ),
        )
        result = evaluate_R005(case)
        assert result.violated is True
        assert result.confidence == 1.0

    def test_llm_fallback_when_terminal_notes_present(self, mocker) -> None:
        """R005 falls back to LLM when gate timestamps are absent but notes exist."""
        from data.schemas.case import TerminalRecord
        mock_client = _mock_llm(
            mocker, _RULES_LLM_PATH,
            violated=True, confidence=0.82,
            reasoning="Notes show gate-in at 07:15, 45 min before invoice start.",
        )
        rec = TerminalRecord(
            record_id="TOS-001",
            event_type="other",
            event_timestamp=datetime(2024, 5, 30, 8, 0, tzinfo=timezone.utc),
            container_number="ATSU1234567",
            source="TRAPAC_LA",
            notes="Gate in recorded at 07:15 on 30-May-2024.",
        )
        case = make_case(evidence_overrides=dict(
            gate_in_timestamp=None,
            gate_out_timestamp=None,
            terminal_records=[rec],
        ))
        result = evaluate_R005(case)
        mock_client.with_structured_output.assert_called_once()
        assert result.violated is True

    def test_cannot_evaluate_no_timestamps_no_notes(self) -> None:
        """R005 cannot_evaluate when no gate timestamps and no terminal record notes."""
        case = make_case(evidence_overrides=dict(
            gate_in_timestamp=None,
            gate_out_timestamp=None,
            terminal_records=[],
        ))
        result = evaluate_R005(case)
        assert result.violated is None
        assert result.missing_evidence is not None

    def test_no_llm_when_deterministic_resolves(self, mocker) -> None:
        """R005 does NOT call LLM when structured gate timestamps are available."""
        mock_client = mocker.patch(_RULES_LLM_PATH)
        case = make_case()  # has gate_in/gate_out timestamps by default
        evaluate_R005(case)
        mock_client.assert_not_called()


# ===========================================================================
# Phase C cross-rule: citation and RuleResult contracts
# ===========================================================================

@pytest.mark.parametrize("evaluate_fn, rule_id", [
    (evaluate_R008, "R008"),
])
def test_phase_c_citation_field_matches_registry(evaluate_fn, rule_id) -> None:
    """Phase C rules' citation field equals get_citation(rule_id).verbatim_text."""
    from agents.compliance_auditor.citations import get_citation
    case = make_case()
    result = evaluate_fn(case)
    assert result.citation == get_citation(rule_id).verbatim_text


@pytest.mark.parametrize("evaluate_fn", [evaluate_R008])
def test_phase_c_result_is_ruleresult_instance(evaluate_fn) -> None:
    """Phase C no-LLM rules return a RuleResult instance."""
    case = make_case()
    assert isinstance(evaluate_fn(case), RuleResult)


# ===========================================================================
# PHASE D — LLM-primary rules (R006, R007)
# ===========================================================================

# ---------------------------------------------------------------------------
# Shared fixture helpers for Phase D
# ---------------------------------------------------------------------------

def _make_appointment_case(**evidence_overrides):
    """Case with a valid appointment_time for R006 evaluation."""
    return make_case(evidence_overrides=dict(
        appointment_time=datetime(2024, 5, 30, 8, 0, tzinfo=timezone.utc),
        **evidence_overrides,
    ))


def _make_force_majeure_case(**evidence_overrides):
    """Case with a weather event and matching free-time window for R007 evaluation."""
    return make_case(
        invoice_overrides=dict(
            free_time_start=datetime(2024, 5, 30, 8, 0, tzinfo=timezone.utc),
            free_time_end=datetime(2024, 5, 30, 12, 0, tzinfo=timezone.utc),
            hours_billed=4.0,
            total_amount_usd=300.0,
        ),
        evidence_overrides=dict(
            weather_events=["Tornado warning EF1: 2024-05-30 09:00–12:00 UTC, "
                            "NOAA advisory, terminal evacuated."],
            **evidence_overrides,
        ),
    )


# ===========================================================================
# R006 — Appointment compliance
# ===========================================================================

class TestR006AppointmentCompliance:
    """R006: LLM-primary, evidence-gated by appointment_time or appt records."""

    def test_clear_violation_llm(self, mocker) -> None:
        """R006 clear_violation: LLM returns violated=True, confidence in band."""
        _mock_llm(mocker, _RULES_LLM_PATH,
                  violated=True, confidence=0.9,
                  reasoning="Gate denied the truck despite a valid appointment. "
                             "Carrier caused the delay.")
        case = _make_appointment_case()
        result = evaluate_R006(case)
        assert result.violated is True
        assert 0.6 <= result.confidence <= 0.95
        assert result.rule_id == "R006"

    def test_clear_pass_llm(self, mocker) -> None:
        """R006 clear_pass: LLM returns violated=False, confidence in band."""
        _mock_llm(mocker, _RULES_LLM_PATH,
                  violated=False, confidence=0.85,
                  reasoning="Truck arrived 3 hours late; receiver was responsible.")
        case = _make_appointment_case()
        result = evaluate_R006(case)
        assert result.violated is False
        assert 0.6 <= result.confidence <= 0.95

    def test_borderline_llm(self, mocker) -> None:
        """R006 borderline: confidence 0.65 is in band; either violated value OK."""
        _mock_llm(mocker, _RULES_LLM_PATH,
                  violated=True, confidence=0.65,
                  reasoning="Ambiguous record — some evidence of gate delay.")
        case = _make_appointment_case()
        result = evaluate_R006(case)
        assert result.violated in (True, False, None)
        assert result.confidence == pytest.approx(0.65)

    def test_cannot_evaluate_no_evidence(self) -> None:
        """R006 cannot_evaluate when appointment_time is None and no appt records."""
        case = make_case(evidence_overrides=dict(
            appointment_time=None,
            terminal_records=[],
        ))
        result = evaluate_R006(case)
        assert result.violated is None
        assert result.missing_evidence is not None
        paths = {m.field_path for m in result.missing_evidence}
        assert "evidence.appointment_time" in paths

    def test_cannot_evaluate_partial_evidence_wrong_record_type(self) -> None:
        """R006 cannot_evaluate when terminal_records exist but are not appointment_scheduled."""
        from data.schemas.case import TerminalRecord
        rec = TerminalRecord(
            record_id="TOS-002",
            event_type="gate_in",
            event_timestamp=datetime(2024, 5, 30, 8, 10, tzinfo=timezone.utc),
            container_number="ATSU1234567",
            source="TRAPAC_LA",
        )
        case = make_case(evidence_overrides=dict(
            appointment_time=None,
            terminal_records=[rec],
        ))
        result = evaluate_R006(case)
        assert result.violated is None  # gate_in record doesn't satisfy evidence gate

    def test_confidence_clamping_low_returns_cannot_evaluate(self, mocker) -> None:
        """R006: LLM confidence 0.4 causes the rule to return violated=None."""
        _mock_llm(mocker, _RULES_LLM_PATH,
                  violated=True, confidence=0.4,
                  reasoning="Extremely ambiguous; could not determine causation.")
        case = _make_appointment_case()
        result = evaluate_R006(case)
        assert result.violated is None

    def test_confidence_clamping_high(self, mocker) -> None:
        """R006: LLM confidence 1.0 is clamped to 0.95."""
        _mock_llm(mocker, _RULES_LLM_PATH,
                  violated=True, confidence=1.0,
                  reasoning="Gate clearly denied on-time truck.")
        case = _make_appointment_case()
        result = evaluate_R006(case)
        assert result.confidence == 0.95

    def test_prompt_includes_citation(self, mocker) -> None:
        """R006 prompt sent to LLM contains §541.6(e)(2) verbatim text."""
        from agents.compliance_auditor.citations import get_citation
        expected_text = get_citation("R006").verbatim_text

        captured_messages = []
        mock_client = mocker.MagicMock()
        mock_chain = mocker.MagicMock()

        def capture_invoke(messages):
            captured_messages.extend(messages)
            return mocker.MagicMock(violated=False, confidence=0.8, reasoning="OK")

        mock_chain.invoke = capture_invoke
        mock_client.with_structured_output.return_value = mock_chain
        mocker.patch(_RULES_LLM_PATH, return_value=mock_client)

        case = _make_appointment_case()
        evaluate_R006(case)

        full_text = " ".join(str(m.content) for m in captured_messages)
        assert expected_text[:60] in full_text, (
            f"Citation not found in R006 prompt. First 60 chars: {expected_text[:60]!r}"
        )

    def test_no_llm_call_when_cannot_evaluate(self, mocker) -> None:
        """R006 does not call LLM when evidence gate fails."""
        mock_client = mocker.patch(_RULES_LLM_PATH)
        case = make_case(evidence_overrides=dict(appointment_time=None, terminal_records=[]))
        evaluate_R006(case)
        mock_client.assert_not_called()


# ===========================================================================
# R007 — Force majeure events
# ===========================================================================

class TestR007ForceMajeure:
    """R007: LLM-primary; incentive-function test from 85 FR 29638."""

    def test_clear_violation_llm(self, mocker) -> None:
        """R007 clear_violation: LLM returns violated=True, confidence in band."""
        _mock_llm(mocker, _RULES_LLM_PATH,
                  violated=True, confidence=0.92,
                  reasoning="Tornado warning EF1 overlapped entire billed window; "
                             "terminal was evacuated and retrieval was impossible. "
                             "Charge cannot serve incentive function per 85 FR 29638.")
        case = _make_force_majeure_case()
        result = evaluate_R007(case)
        assert result.violated is True
        assert 0.6 <= result.confidence <= 0.95
        assert result.rule_id == "R007"

    def test_clear_pass_llm(self, mocker) -> None:
        """R007 clear_pass: LLM returns violated=False when no valid overlap."""
        _mock_llm(mocker, _RULES_LLM_PATH,
                  violated=False, confidence=0.88,
                  reasoning="Weather event occurred after free-time window ended; "
                             "no overlap with billed period.")
        case = _make_force_majeure_case()
        result = evaluate_R007(case)
        assert result.violated is False
        assert 0.6 <= result.confidence <= 0.95

    def test_borderline_llm(self, mocker) -> None:
        """R007 borderline: confidence 0.65 is in band."""
        _mock_llm(mocker, _RULES_LLM_PATH,
                  violated=True, confidence=0.65,
                  reasoning="Partial overlap; receiver may have had alternative access.")
        case = _make_force_majeure_case()
        result = evaluate_R007(case)
        assert result.confidence == pytest.approx(0.65)

    def test_cannot_evaluate_no_force_majeure_data(self) -> None:
        """R007 cannot_evaluate when no weather events and no terminal_closure records."""
        case = make_case(evidence_overrides=dict(
            weather_events=[],
            terminal_records=[],
        ))
        result = evaluate_R007(case)
        assert result.violated is None
        assert result.missing_evidence is not None

    def test_cannot_evaluate_partial_evidence_missing_timestamps(self) -> None:
        """R007 cannot_evaluate when weather events present but free_time window absent."""
        case = make_case(
            invoice_overrides=dict(free_time_start=None, free_time_end=None),
            evidence_overrides=dict(
                weather_events=["Hurricane warning during port closure."],
            ),
        )
        result = evaluate_R007(case)
        assert result.violated is None
        assert result.missing_evidence is not None
        paths = {m.field_path for m in result.missing_evidence}
        assert any("free_time" in p for p in paths)

    def test_confidence_clamping_low_returns_cannot_evaluate(self, mocker) -> None:
        """R007: LLM confidence 0.4 causes violated=None."""
        _mock_llm(mocker, _RULES_LLM_PATH,
                  violated=True, confidence=0.4,
                  reasoning="Event duration unclear; cannot establish overlap.")
        case = _make_force_majeure_case()
        result = evaluate_R007(case)
        assert result.violated is None

    def test_confidence_clamping_high(self, mocker) -> None:
        """R007: LLM confidence 1.0 is clamped to 0.95."""
        _mock_llm(mocker, _RULES_LLM_PATH,
                  violated=True, confidence=1.0,
                  reasoning="Terminal closed by government order for entire billed period.")
        case = _make_force_majeure_case()
        result = evaluate_R007(case)
        assert result.confidence == 0.95

    def test_prompt_includes_primary_citation(self, mocker) -> None:
        """R007 prompt contains §541.6(e)(2) verbatim text."""
        from agents.compliance_auditor.citations import get_citation
        expected_text = get_citation("R007").verbatim_text

        captured_messages = []
        mock_client = mocker.MagicMock()
        mock_chain = mocker.MagicMock()

        def capture_invoke(messages):
            captured_messages.extend(messages)
            return mocker.MagicMock(violated=True, confidence=0.8, reasoning="FM overlap")

        mock_chain.invoke = capture_invoke
        mock_client.with_structured_output.return_value = mock_chain
        mocker.patch(_RULES_LLM_PATH, return_value=mock_client)

        case = _make_force_majeure_case()
        evaluate_R007(case)

        full_text = " ".join(str(m.content) for m in captured_messages)
        assert expected_text[:60] in full_text, (
            f"Primary citation not found in R007 prompt. First 60: {expected_text[:60]!r}"
        )

    def test_prompt_includes_supporting_authority(self, mocker) -> None:
        """R007 prompt contains 85 FR 29638 supporting authority verbatim text."""
        from agents.compliance_auditor.citations import get_citation
        supporting_text = get_citation("R007").supporting_authorities[0].verbatim_text
        # First 60 chars should appear in the prompt
        expected_fragment = supporting_text[:60]

        captured_messages = []
        mock_client = mocker.MagicMock()
        mock_chain = mocker.MagicMock()

        def capture_invoke(messages):
            captured_messages.extend(messages)
            return mocker.MagicMock(violated=True, confidence=0.85, reasoning="FM")

        mock_chain.invoke = capture_invoke
        mock_client.with_structured_output.return_value = mock_chain
        mocker.patch(_RULES_LLM_PATH, return_value=mock_client)

        case = _make_force_majeure_case()
        evaluate_R007(case)

        full_text = " ".join(str(m.content) for m in captured_messages)
        assert expected_fragment in full_text, (
            f"Supporting authority not found in R007 prompt. "
            f"First 60 chars: {expected_fragment!r}"
        )

    def test_citation_field_is_primary_only(self, mocker) -> None:
        """R007 RuleResult.citation carries only §541.6(e)(2) text, not 85 FR 29638."""
        from agents.compliance_auditor.citations import get_citation
        _mock_llm(mocker, _RULES_LLM_PATH,
                  violated=True, confidence=0.85, reasoning="FM overlap confirmed.")
        case = _make_force_majeure_case()
        result = evaluate_R007(case)
        primary_text = get_citation("R007").verbatim_text
        supporting_text = get_citation("R007").supporting_authorities[0].verbatim_text
        assert result.citation == primary_text
        assert supporting_text not in result.citation

    def test_no_llm_call_when_cannot_evaluate(self, mocker) -> None:
        """R007 does not call LLM when evidence gate fails."""
        mock_client = mocker.patch(_RULES_LLM_PATH)
        case = make_case(evidence_overrides=dict(weather_events=[], terminal_records=[]))
        evaluate_R007(case)
        mock_client.assert_not_called()

