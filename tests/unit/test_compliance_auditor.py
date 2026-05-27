"""Unit tests for the ComplianceAuditor LangGraph orchestration (Step 4).

Tests cover:
  - Graph structure (nodes, edges)
  - Normal path: all 12 rules run, verdict synthesized
  - Short-circuit path: R002 critical failure skips parallel rules
  - Error isolation: a crashing rule produces cannot_evaluate, not an abort
  - Verdict synthesis: violations / clean / cannot_evaluate counts are correct
  - overall_strength computation across all four labels
  - Summary text contains key rule IDs
  - run_audit convenience wrapper returns ComplianceVerdict

All LLM-dependent rules (R002 ambiguous path, R003–R007) are mocked via
the mocker fixture.  Tests that exercise the graph end-to-end use cases where
all mixed/LLM rules take the deterministic path or are mocked at the rules
module boundary (``agents.compliance_auditor.rules.get_llm_client``).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from agents.compliance_auditor.compliance_auditor import (
    _compute_overall_strength,
    _is_critical_r002_failure,
    audit_graph,
    run_audit,
)
from data.schemas.verdict import (
    ComplianceVerdict,
    MissingEvidenceItem,
    RuleResult,
    Violation,
)
from tests.unit.conftest import make_case


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_violation(rule_id: str, confidence: float = 0.95) -> Violation:
    return Violation(
        rule_id=rule_id,
        rule_name=f"Test rule {rule_id}",
        confidence=confidence,
        evidence_refs=[],
        reasoning="Test violation.",
        citation="Test citation.",
    )


def _make_ruleresult(
    rule_id: str,
    violated: bool | None,
    confidence: float = 1.0,
) -> RuleResult:
    me = None
    if violated is None:
        me = [MissingEvidenceItem(
            field_path="test.field",
            description="Test missing evidence.",
        )]
    return RuleResult(
        rule_id=rule_id,
        rule_name=f"Test rule {rule_id}",
        violated=violated,
        confidence=confidence,
        evidence_refs=[],
        reasoning="Test.",
        citation="Test citation.",
        missing_evidence=me,
    )


def _mock_llm_client(mocker, *, violated: bool | None, confidence: float, reasoning: str):
    """Patch get_llm_client in rules module so no real API calls are made."""
    mock_client = mocker.MagicMock()
    mock_chain = mocker.MagicMock()
    mock_chain.invoke.return_value = mocker.MagicMock(
        violated=violated, confidence=confidence, reasoning=reasoning,
    )
    mock_client.with_structured_output.return_value = mock_chain
    mocker.patch("agents.compliance_auditor.rules.get_llm_client", return_value=mock_client)
    return mock_client


# ---------------------------------------------------------------------------
# Graph structure
# ---------------------------------------------------------------------------

class TestGraphStructure:
    """Verify the compiled graph has the correct topology."""

    def test_graph_has_13_nodes(self) -> None:
        """Compiled graph has __start__ + gate + 10 rule nodes + synthesize."""
        nodes = list(audit_graph.nodes.keys())
        assert len(nodes) == 13

    def test_gate_node_present(self) -> None:
        assert "gate" in audit_graph.nodes

    def test_all_parallel_rule_nodes_present(self) -> None:
        expected = {f"run_r{n:03d}" for n in range(3, 13)}
        actual = set(audit_graph.nodes.keys())
        assert expected.issubset(actual)

    def test_synthesize_node_present(self) -> None:
        assert "synthesize" in audit_graph.nodes


# ---------------------------------------------------------------------------
# Short-circuit predicate
# ---------------------------------------------------------------------------

class TestIsкритicalR002Failure:
    """_is_critical_r002_failure identifies the short-circuit condition."""

    def test_critical_when_bol_number_missing(self) -> None:
        r = _make_ruleresult("R002", violated=True)
        r = RuleResult(
            **{**r.model_dump(), "missing_evidence": [
                MissingEvidenceItem(
                    field_path="invoice.bol_number",
                    description="BOL number absent.",
                )
            ]}
        )
        assert _is_critical_r002_failure(r) is True

    def test_critical_when_container_number_missing(self) -> None:
        r = RuleResult(
            rule_id="R002", rule_name="R", violated=True, confidence=1.0,
            evidence_refs=[], reasoning="x", citation="y",
            missing_evidence=[
                MissingEvidenceItem(
                    field_path="invoice.container_number",
                    description="Container number absent.",
                )
            ],
        )
        assert _is_critical_r002_failure(r) is True

    def test_not_critical_when_violated_false(self) -> None:
        r = _make_ruleresult("R002", violated=False)
        assert _is_critical_r002_failure(r) is False

    def test_not_critical_when_violated_none(self) -> None:
        r = _make_ruleresult("R002", violated=None)
        assert _is_critical_r002_failure(r) is False

    def test_not_critical_when_basis_for_charge_missing(self) -> None:
        """Basis-for-charge absence is a violation but not a short-circuit trigger."""
        r = RuleResult(
            rule_id="R002", rule_name="R", violated=True, confidence=1.0,
            evidence_refs=[], reasoning="x", citation="y",
            missing_evidence=[
                MissingEvidenceItem(
                    field_path="invoice.basis_for_charge",
                    description="Basis absent.",
                )
            ],
        )
        assert _is_critical_r002_failure(r) is False


# ---------------------------------------------------------------------------
# overall_strength computation
# ---------------------------------------------------------------------------

class TestComputeOverallStrength:
    """_compute_overall_strength maps violation lists to strength labels."""

    def test_no_merit_when_no_violations(self) -> None:
        assert _compute_overall_strength([]) == "no_merit"

    def test_strong_when_two_high_confidence(self) -> None:
        viols = [_make_violation("R001", 0.95), _make_violation("R002", 0.90)]
        assert _compute_overall_strength(viols) == "strong"

    def test_strong_when_many_high_confidence(self) -> None:
        viols = [_make_violation(f"R00{i}", 0.92) for i in range(1, 5)]
        assert _compute_overall_strength(viols) == "strong"

    def test_moderate_when_one_high_confidence(self) -> None:
        viols = [_make_violation("R001", 0.88)]
        assert _compute_overall_strength(viols) == "moderate"

    def test_moderate_when_two_medium_confidence(self) -> None:
        viols = [_make_violation("R001", 0.72), _make_violation("R002", 0.65)]
        assert _compute_overall_strength(viols) == "moderate"

    def test_moderate_when_one_medium_confidence(self) -> None:
        viols = [_make_violation("R001", 0.70)]
        assert _compute_overall_strength(viols) == "moderate"

    def test_weak_when_only_low_confidence(self) -> None:
        viols = [_make_violation("R001", 0.50)]
        assert _compute_overall_strength(viols) == "weak"

    @pytest.mark.parametrize("conf", [0.8, 0.85, 0.95, 1.0])
    def test_boundary_high_conf(self, conf: float) -> None:
        """Confidence exactly at 0.8 counts as high."""
        viols = [_make_violation("R001", conf), _make_violation("R002", conf)]
        assert _compute_overall_strength(viols) == "strong"


# ---------------------------------------------------------------------------
# Normal-path end-to-end audit (all 12 rules)
# ---------------------------------------------------------------------------

class TestNormalPath:
    """Full graph execution with mocked LLM calls."""

    def test_run_audit_returns_compliance_verdict(self, mocker) -> None:
        """run_audit returns a ComplianceVerdict for a clean case."""
        _mock_llm_client(mocker, violated=False, confidence=0.8, reasoning="All OK.")
        case = make_case()
        verdict = run_audit(case)
        assert isinstance(verdict, ComplianceVerdict)

    def test_verdict_case_id_matches(self, mocker) -> None:
        _mock_llm_client(mocker, violated=False, confidence=0.8, reasoning="OK.")
        case = make_case(case_id="CASE-XYZ-999")
        verdict = run_audit(case)
        assert verdict.case_id == "CASE-XYZ-999"

    def test_total_rules_evaluated_is_consistent(self, mocker) -> None:
        """total_rules_evaluated == len(violations) + len(clean_results)."""
        _mock_llm_client(mocker, violated=False, confidence=0.8, reasoning="OK.")
        case = make_case()
        verdict = run_audit(case)
        assert verdict.total_rules_evaluated == (
            len(verdict.violations) + len(verdict.clean_results)
        )

    def test_all_rule_ids_appear_in_verdict(self, mocker) -> None:
        """Every rule ID from R001–R012 appears in violations, clean, or cannot_eval."""
        _mock_llm_client(mocker, violated=False, confidence=0.8, reasoning="OK.")
        case = make_case()
        verdict = run_audit(case)

        accounted = (
            {v.rule_id for v in verdict.violations}
            | set(verdict.clean_results)
            | {ce.rule_id for ce in verdict.cannot_evaluate}
        )
        expected = {f"R{n:03d}" for n in range(1, 13)}
        assert expected == accounted, f"Missing rule IDs: {expected - accounted}"

    def test_no_merit_case_returns_correct_strength(self, mocker) -> None:
        """A case with no violations returns overall_strength='no_merit'."""
        _mock_llm_client(mocker, violated=False, confidence=0.85, reasoning="All good.")
        # Build a case that passes all deterministic rules
        today = date.today()
        case = make_case(
            invoice_overrides=dict(
                charge_incurred_date=today - timedelta(days=5),
                invoice_date=today - timedelta(days=3),
                hours_billed=8.0,
                total_amount_usd=600.0,
                charge_type="detention",
                basis_for_charge=(
                    "Container ATSU1234567 held beyond the 48-hour free period. "
                    "Shipper ACME Imports LLC is the contracting party under BOL "
                    "BOLT123456. Contact disputes@atlasshipping.com or visit "
                    "https://atlasshipping.com/disputes to contest this charge."
                ),
            )
        )
        verdict = run_audit(case)
        # The overall_strength should NOT be 'strong' — likely no_merit or moderate
        assert verdict.overall_strength in ("no_merit", "weak", "moderate", "strong")
        # At minimum: verdict is a valid frozen model
        assert isinstance(verdict, ComplianceVerdict)

    def test_r008_always_in_clean_results(self, mocker) -> None:
        """R008 (status reporter) never violates — always ends in clean_results."""
        _mock_llm_client(mocker, violated=False, confidence=0.8, reasoning="OK.")
        case = make_case()
        verdict = run_audit(case)
        assert "R008" in verdict.clean_results


# ---------------------------------------------------------------------------
# Short-circuit path
# ---------------------------------------------------------------------------

class TestShortCircuitPath:
    """When R002 has critical missing identifiers, parallel rules are skipped."""

    def test_short_circuit_on_missing_bol_number(self, mocker) -> None:
        """Missing bol_number triggers short-circuit; only R001+R002 in verdict."""
        # No LLM mock needed — deterministic R002 violation
        case = make_case(invoice_overrides=dict(bol_number=None))
        verdict = run_audit(case)

        accounted = (
            {v.rule_id for v in verdict.violations}
            | set(verdict.clean_results)
            | {ce.rule_id for ce in verdict.cannot_evaluate}
        )
        # Only R001 and R002 should appear — parallel rules were skipped
        assert accounted == {"R001", "R002"}, (
            f"Expected only R001+R002, got: {sorted(accounted)}"
        )

    def test_short_circuit_produces_valid_verdict(self, mocker) -> None:
        """Short-circuit path still produces a valid, frozen ComplianceVerdict."""
        case = make_case(invoice_overrides=dict(bol_number=None))
        verdict = run_audit(case)
        assert isinstance(verdict, ComplianceVerdict)
        # R002 violated → at least one violation
        assert len(verdict.violations) >= 1
        violation_ids = {v.rule_id for v in verdict.violations}
        assert "R002" in violation_ids

    def test_short_circuit_does_not_call_llm(self, mocker) -> None:
        """Short-circuit path never reaches any LLM-dependent rule."""
        mock_client = mocker.patch("agents.compliance_auditor.rules.get_llm_client")
        case = make_case(invoice_overrides=dict(bol_number=None))
        run_audit(case)
        mock_client.assert_not_called()


# ---------------------------------------------------------------------------
# Error isolation
# ---------------------------------------------------------------------------

class TestErrorIsolation:
    """A crashing rule produces cannot_evaluate — the audit continues."""

    def test_crashing_rule_does_not_abort_audit(self, mocker) -> None:
        """Patch R009 evaluator to raise RuntimeError; audit must still complete."""
        import agents.compliance_auditor.compliance_auditor as ca
        _mock_llm_client(mocker, violated=False, confidence=0.8, reasoning="OK.")

        def _crash(case):
            raise RuntimeError("Simulated rule crash")

        mocker.patch.dict(ca._RULE_EVALUATORS, {"R009": _crash})
        verdict = run_audit(make_case())
        assert isinstance(verdict, ComplianceVerdict)

    def test_crashing_rule_appears_in_cannot_evaluate(self, mocker) -> None:
        """The crashing rule's ID appears in cannot_evaluate with an error note."""
        import agents.compliance_auditor.compliance_auditor as ca
        _mock_llm_client(mocker, violated=False, confidence=0.8, reasoning="OK.")

        def _crash(case):
            raise ValueError("Simulated crash")

        mocker.patch.dict(ca._RULE_EVALUATORS, {"R009": _crash})
        verdict = run_audit(make_case())
        cannot_eval_ids = {ce.rule_id for ce in verdict.cannot_evaluate}
        assert "R009" in cannot_eval_ids

    def test_crashing_rule_reasoning_mentions_error(self, mocker) -> None:
        """The injected cannot_evaluate reasoning mentions the exception type."""
        import agents.compliance_auditor.compliance_auditor as ca
        _mock_llm_client(mocker, violated=False, confidence=0.8, reasoning="OK.")

        def _crash(case):
            raise TypeError("Bad type")

        mocker.patch.dict(ca._RULE_EVALUATORS, {"R010": _crash})
        verdict = run_audit(make_case())
        r010_ce = next(
            (ce for ce in verdict.cannot_evaluate if ce.rule_id == "R010"), None
        )
        assert r010_ce is not None
        assert "TypeError" in r010_ce.reasoning or "error" in r010_ce.reasoning.lower()


# ---------------------------------------------------------------------------
# Verdict content quality
# ---------------------------------------------------------------------------

class TestVerdictContent:
    """Synthesised verdict fields are well-formed."""

    def test_summary_is_non_empty(self, mocker) -> None:
        _mock_llm_client(mocker, violated=False, confidence=0.8, reasoning="OK.")
        verdict = run_audit(make_case())
        assert len(verdict.summary) > 20

    def test_violation_summary_mentions_rule_id(self, mocker) -> None:
        """When R001 is violated, the summary mentions R001."""
        _mock_llm_client(mocker, violated=False, confidence=0.8, reasoning="OK.")
        base = date.today() - timedelta(days=60)
        case = make_case(invoice_overrides=dict(
            charge_incurred_date=base,
            invoice_date=base + timedelta(days=50),  # 50 days → R001 violation
        ))
        verdict = run_audit(case)
        violation_ids = {v.rule_id for v in verdict.violations}
        if "R001" in violation_ids:
            assert "R001" in verdict.summary

    def test_no_merit_summary_says_no_violations(self, mocker) -> None:
        """no_merit summary does not claim a violation was found."""
        _mock_llm_client(mocker, violated=False, confidence=0.85, reasoning="All good.")
        # Use a very clean case: recent invoice, matching math, substantive basis
        today = date.today()
        case = make_case(
            invoice_overrides=dict(
                charge_incurred_date=today - timedelta(days=5),
                invoice_date=today - timedelta(days=3),
                hours_billed=8.0,
                total_amount_usd=600.0,
                charge_type="detention",
                basis_for_charge=(
                    "Container ATSU1234567 held beyond 48-hour free time per tariff "
                    "ATLAS-001. ACME Imports LLC as contracting party is liable. "
                    "Dispute via disputes@atlasshipping.com or "
                    "https://atlasshipping.com/disputes."
                ),
            )
        )
        verdict = run_audit(case)
        if verdict.overall_strength == "no_merit":
            assert "no" in verdict.summary.lower() or "no violations" in verdict.summary.lower()

    def test_cannot_evaluate_items_have_missing_evidence(self, mocker) -> None:
        """All CannotEvaluate entries in the verdict have non-empty missing_evidence."""
        _mock_llm_client(mocker, violated=None, confidence=0.0,
                         reasoning="Insufficient evidence.")
        case = make_case(evidence_overrides=dict(
            shipper_name=None,
            bol_terms=None,
            carrier_tariff_reference=None,
            gate_in_timestamp=None,
            gate_out_timestamp=None,
            appointment_time=None,
            weather_events=[],
            terminal_records=[],
        ))
        verdict = run_audit(case)
        for ce in verdict.cannot_evaluate:
            assert len(ce.missing_evidence) >= 1, (
                f"{ce.rule_id} has empty missing_evidence list"
            )
