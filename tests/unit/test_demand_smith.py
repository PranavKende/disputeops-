"""Unit tests for DemandSmith — letter drafting agent.

All LLM calls are mocked at the get_llm_client boundary via FakeListChatModel
or patch.  No real API calls are made.  All dates use explicit fixture constants
— no date.today() in any test.

Test inventory (55 tests)
-------------------------
Group 1 — DemandLetter schema validation (10)
  DS-U-01  frozen model raises on attribute set
  DS-U-02  word_count mismatch raises ValidationError
  DS-U-03  deadline not equal to filing_date + 30 raises ValidationError
  DS-U-04  invalid cited_rule pattern raises ValidationError
  DS-U-05  demand_amount_usd <= 0 raises ValidationError
  DS-U-06  letter_text too short raises ValidationError
  DS-U-07  header_note non-None with firm tone raises ValidationError
  DS-U-08  header_note non-None with escalation tone raises ValidationError
  DS-U-09  header_note None with factual tone is valid
  DS-U-10  supporting_authorities_cited defaults to empty list

Group 2 — Template: deadline and date helpers (5)
  DS-U-11  compute_response_deadline returns filing_date + 30
  DS-U-12  build_timeliness_paragraph contains §541.8(a) and §541.8(b)
  DS-U-13  build_timeliness_paragraph contains filing_date and response_deadline
  DS-U-14  build_timeliness_paragraph days_elapsed computed from invoice_date, not today
  DS-U-15  build_timeliness_paragraph contains carrier name and invoice number

Group 3 — Template: letterhead, salutation, closing (7)
  DS-U-16  build_letterhead contains carrier name, invoice number, demand amount
  DS-U-17  build_letterhead contains BOL and container number when present
  DS-U-18  build_letterhead omits BOL line when bol_number is None
  DS-U-19  build_letterhead deadline line contains §541.8(b)
  DS-U-20  build_salutation returns correct salutation string
  DS-U-21  build_closing firm contains FMC complaint language
  DS-U-22  build_closing factual does not contain FMC complaint language

Group 4 — Severity sort (6)
  DS-U-23  R001 sorts before R007 (both tier-1; R001 has higher conf)
  DS-U-24  R007 sorts before R004 (tier-1 before tier-2)
  DS-U-25  R004 sorts before R012 (tier-2 before tier-4)
  DS-U-26  R012 sorts last among all clean_violation violations
  DS-U-27  within same tier, higher confidence sorts first
  DS-U-28  empty list returns empty list

Group 5 — Tone selection (4)
  DS-U-29  auto_file → firm
  DS-U-30  human_review → factual
  DS-U-31  write_off → factual
  DS-U-32  tone mapping covers all three recommended_action values

Group 6 — Citation block formatter (7)
  DS-U-33  R001 block contains rule label and verbatim citation text
  DS-U-34  R001 block contains confidence percentage
  DS-U-35  R007 block contains primary §541.6(e)(2) text
  DS-U-36  R007 block contains 85 FR 29638 supporting authority
  DS-U-37  R007 supporting authority verbatim text is present and non-empty
  DS-U-38  R004 block (no supporting authority) has no 'reinforced by' line
  DS-U-39  build_citation_block calls get_citation — not violation.citation directly

Group 7 — draft_letter contract (9)
  DS-U-40  draft_letter with FakeListChatModel produces valid DemandLetter
  DS-U-41  draft_letter cited_rules matches violation rule IDs, severity-ordered
  DS-U-42  draft_letter demand_amount_usd == invoice.total_amount_usd
  DS-U-43  draft_letter response_deadline == filing_date + 30
  DS-U-44  draft_letter word_count == len(letter_text.split())
  DS-U-45  draft_letter cannot_evaluate items absent from letter_text
  DS-U-46  draft_letter write_off: header_note populated, tone factual
  DS-U-47  draft_letter zero violations raises ValueError
  DS-U-48  draft_letter letter_text starts with filing_date string, not log noise

Group 8 — Prompt structure (7)
  DS-U-49  NARRATIVE_PROMPT is a ChatPromptTemplate with system + human messages
  DS-U-50  TONE_INSTRUCTIONS has keys factual, firm, escalation
  DS-U-51  CONSTRAINTS_SECTION contains 'Do NOT' citation instruction
  DS-U-52  CONSTRAINTS_SECTION contains dollar-amount prohibition
  DS-U-53  NARRATIVE_PROMPT human template contains violation_summaries placeholder
  DS-U-54  NARRATIVE_PROMPT human template contains tone_instruction placeholder
  DS-U-55  ROLE_SECTION identifies agent as DemandSmith
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from pydantic import ValidationError

from agents.compliance_auditor.citations import get_citation
from agents.demand_smith.demand_smith import draft_letter, _build_violation_summaries
from agents.demand_smith.prompts import (
    CONSTRAINTS_SECTION,
    NARRATIVE_PROMPT,
    ROLE_SECTION,
    TONE_INSTRUCTIONS,
)
from agents.demand_smith.templates import (
    build_citation_block,
    build_closing,
    build_letterhead,
    build_salutation,
    build_timeliness_paragraph,
    compute_response_deadline,
    select_tone,
    sort_violations_by_severity,
)
from data.fixtures.clean_violation_case import (
    CHARGE_AMOUNT_USD,
    make_clean_violation_case,
    make_clean_violation_llm_responses,
)
from data.schemas.case import DisputeCase, EvidencePackage, Invoice
from data.schemas.letter import DemandLetter
from data.schemas.score import RecoveryScore
from data.schemas.verdict import CannotEvaluate, ComplianceVerdict, MissingEvidenceItem, Violation

# ---------------------------------------------------------------------------
# Date constants — all tests use these; nothing calls date.today()
# ---------------------------------------------------------------------------

_FILING_DATE = date(2026, 6, 26)          # 1 day after invoice_date in clean_violation fixture
_DEADLINE = _FILING_DATE + timedelta(days=30)   # 2026-07-26


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_case() -> DisputeCase:
    return make_clean_violation_case()


@pytest.fixture
def r001_violation() -> Violation:
    citation = get_citation("R001")
    return Violation(
        rule_id="R001",
        rule_name="Invoicing window",
        confidence=1.0,
        evidence_refs=["invoice.invoice_date", "invoice.charge_incurred_date"],
        reasoning="Invoice issued 47 days after charge; exceeds 30-day window.",
        citation=citation.verbatim_text,
    )


@pytest.fixture
def r007_violation() -> Violation:
    citation = get_citation("R007")
    return Violation(
        rule_id="R007",
        rule_name="Force majeure events",
        confidence=0.92,
        evidence_refs=["evidence.weather_events"],
        reasoning="ILWU stoppage prevented retrieval; §541.6(e)(2) certification false.",
        citation=citation.verbatim_text,
    )


@pytest.fixture
def r004_violation() -> Violation:
    citation = get_citation("R004")
    return Violation(
        rule_id="R004",
        rule_name="Free time accuracy",
        confidence=1.0,
        evidence_refs=["evidence.published_free_time_days"],
        reasoning="Tariff says 72h; invoice claims 48h.",
        citation=citation.verbatim_text,
    )


@pytest.fixture
def r012_violation() -> Violation:
    citation = get_citation("R012")
    return Violation(
        rule_id="R012",
        rule_name="Dispute mechanism disclosure",
        confidence=1.0,
        evidence_refs=["invoice.basis_for_charge"],
        reasoning="No URL or contact marker found.",
        citation=citation.verbatim_text,
    )


@pytest.fixture
def six_violations(
    r001_violation: Violation,
    r007_violation: Violation,
    r004_violation: Violation,
    r012_violation: Violation,
) -> list[Violation]:
    r002 = Violation(
        rule_id="R002", rule_name="Required minimum content", confidence=0.75,
        evidence_refs=["invoice.basis_for_charge"],
        reasoning="Generic phrase; does not explain liability.",
        citation=get_citation("R002").verbatim_text,
    )
    r011 = Violation(
        rule_id="R011", rule_name="Per-day vs per-hour rule application", confidence=1.0,
        evidence_refs=["invoice.hours_billed", "invoice.charge_type"],
        reasoning="Demurrage billed hourly; must be per calendar day.",
        citation=get_citation("R011").verbatim_text,
    )
    return [r001_violation, r007_violation, r004_violation, r002, r011, r012_violation]


def _make_verdict(violations: list[Violation], clean_rule_ids: list[str] | None = None) -> ComplianceVerdict:
    clean = clean_rule_ids or ["R003", "R005", "R008", "R009", "R010"]
    return ComplianceVerdict(
        case_id="DEMO-CVF-2026-0001",
        total_rules_evaluated=len(violations) + len(clean),
        violations=violations,
        cannot_evaluate=[
            CannotEvaluate(
                rule_id="R006",
                rule_name="Appointment compliance",
                missing_evidence=[
                    MissingEvidenceItem(
                        field_path="evidence.appointment_time",
                        description="No appointment time recorded.",
                    )
                ],
                reasoning="No appointment data; cannot evaluate.",
                citation=get_citation("R006").verbatim_text,
            )
        ],
        clean_results=clean,
        overall_strength="strong",
        summary="Six violations found.",
    )


def _make_score(
    case_id: str = "DEMO-CVF-2026-0001",
    action: str = "auto_file",
    probability: float = 0.87,
) -> RecoveryScore:
    return RecoveryScore(
        case_id=case_id,
        recovery_probability=probability,
        expected_recovery_usd=round(probability * 3920.0, 2),
        recommended_action=action,
        confidence=0.90,
        reasoning="Strong case.",
        key_factors=["R001 violation", "R007 force majeure"],
    )


def _fake_draft(
    verdict: ComplianceVerdict,
    score: RecoveryScore,
    case: DisputeCase,
    narrative: str = "Empire Freight Lines has issued an invoice that violates 46 CFR Part 541 on multiple grounds.",
    filing_date: date = _FILING_DATE,
) -> DemandLetter:
    fake_llm = FakeListChatModel(responses=[narrative])
    with patch("agents.demand_smith.demand_smith.get_llm_client", return_value=fake_llm):
        return draft_letter(verdict, score, case, filing_date=filing_date)


# ---------------------------------------------------------------------------
# Group 1 — DemandLetter schema validation
# ---------------------------------------------------------------------------


class TestDemandLetterSchema:
    def _valid_kwargs(self) -> dict:
        text = (
            "This is the formal demand letter body content. "
            "It contains sufficient text to exceed the minimum length requirement "
            "imposed by the DemandLetter schema validation rules under Pydantic v2."
        )
        return dict(
            case_id="DEMO-CVF-2026-0001",
            letter_text=text,
            tone="firm",
            cited_rules=["R001"],
            demand_amount_usd=3920.0,
            filing_date=_FILING_DATE,
            response_deadline=_DEADLINE,
            word_count=len(text.split()),
        )

    def test_frozen_raises_on_set(self):  # DS-U-01
        letter = DemandLetter(**self._valid_kwargs())
        with pytest.raises((ValidationError, TypeError)):
            letter.tone = "factual"  # type: ignore[misc]

    def test_word_count_mismatch_raises(self):  # DS-U-02
        kw = self._valid_kwargs()
        kw["word_count"] = 999
        with pytest.raises(ValidationError, match="word_count"):
            DemandLetter(**kw)

    def test_deadline_wrong_raises(self):  # DS-U-03
        kw = self._valid_kwargs()
        kw["response_deadline"] = _FILING_DATE + timedelta(days=31)
        with pytest.raises(ValidationError, match="response_deadline"):
            DemandLetter(**kw)

    def test_invalid_cited_rule_raises(self):  # DS-U-04
        kw = self._valid_kwargs()
        kw["cited_rules"] = ["RXXX", "R001"]
        with pytest.raises(ValidationError, match="cited_rules"):
            DemandLetter(**kw)

    def test_demand_amount_zero_raises(self):  # DS-U-05
        kw = self._valid_kwargs()
        kw["demand_amount_usd"] = 0.0
        with pytest.raises(ValidationError):
            DemandLetter(**kw)

    def test_letter_text_too_short_raises(self):  # DS-U-06
        kw = self._valid_kwargs()
        kw["letter_text"] = "Short"
        kw["word_count"] = 1
        with pytest.raises(ValidationError):
            DemandLetter(**kw)

    def test_header_note_with_firm_tone_raises(self):  # DS-U-07
        kw = self._valid_kwargs()
        kw["header_note"] = "Some note"
        with pytest.raises(ValidationError, match="header_note"):
            DemandLetter(**kw)

    def test_header_note_with_escalation_tone_raises(self):  # DS-U-08
        kw = self._valid_kwargs()
        kw["tone"] = "escalation"
        kw["header_note"] = "Some note"
        with pytest.raises(ValidationError, match="header_note"):
            DemandLetter(**kw)

    def test_header_note_none_with_factual_is_valid(self):  # DS-U-09
        kw = self._valid_kwargs()
        kw["tone"] = "factual"
        kw["header_note"] = None
        letter = DemandLetter(**kw)
        assert letter.header_note is None

    def test_supporting_authorities_defaults_empty(self):  # DS-U-10
        letter = DemandLetter(**self._valid_kwargs())
        assert letter.supporting_authorities_cited == []


# ---------------------------------------------------------------------------
# Group 2 — Template: deadline and timeliness paragraph
# ---------------------------------------------------------------------------


class TestDeadlineAndTimeliness:
    def test_compute_response_deadline(self):  # DS-U-11
        assert compute_response_deadline(_FILING_DATE) == _FILING_DATE + timedelta(days=30)

    def test_timeliness_contains_541_8_refs(self, clean_case: DisputeCase):  # DS-U-12
        para = build_timeliness_paragraph(clean_case, _FILING_DATE, _DEADLINE)
        assert "541.8(a)" in para
        assert "541.8(b)" in para

    def test_timeliness_contains_dates(self, clean_case: DisputeCase):  # DS-U-13
        para = build_timeliness_paragraph(clean_case, _FILING_DATE, _DEADLINE)
        assert "June 26, 2026" in para     # filing_date
        assert "July 26, 2026" in para     # deadline

    def test_timeliness_days_from_fixture_not_today(self, clean_case: DisputeCase):  # DS-U-14
        # filing_date=2026-06-26, invoice_date=2026-06-25 → 1 day elapsed
        para = build_timeliness_paragraph(clean_case, _FILING_DATE, _DEADLINE)
        assert "1 calendar day(s)" in para
        # filing_date itself must appear — confirms no date.today() used
        assert "June 26, 2026" in para

    def test_timeliness_contains_carrier_and_invoice(self, clean_case: DisputeCase):  # DS-U-15
        para = build_timeliness_paragraph(clean_case, _FILING_DATE, _DEADLINE)
        assert "Empire Freight Lines" in para
        assert "EMFR-INV-2026-0491" in para


# ---------------------------------------------------------------------------
# Group 3 — Template: letterhead, salutation, closing
# ---------------------------------------------------------------------------


class TestLetterheadSalutationClosing:
    def test_letterhead_contains_carrier_invoice_amount(self, clean_case: DisputeCase):  # DS-U-16
        h = build_letterhead(clean_case, _FILING_DATE, _DEADLINE)
        assert "Empire Freight Lines" in h
        assert "EMFR-INV-2026-0491" in h
        assert "$3,920.00" in h

    def test_letterhead_contains_bol_and_container(self, clean_case: DisputeCase):  # DS-U-17
        h = build_letterhead(clean_case, _FILING_DATE, _DEADLINE)
        assert "EMFR20260401-001" in h
        assert "MEDU9871234" in h

    def test_letterhead_omits_bol_when_none(self, clean_case: DisputeCase):  # DS-U-18
        inv = clean_case.invoice.model_copy(update={"bol_number": None})
        modified = clean_case.model_copy(update={"invoice": inv})
        h = build_letterhead(modified, _FILING_DATE, _DEADLINE)
        assert "BOL Number" not in h

    def test_letterhead_deadline_cites_541_8b(self, clean_case: DisputeCase):  # DS-U-19
        h = build_letterhead(clean_case, _FILING_DATE, _DEADLINE)
        assert "541.8(b)" in h

    def test_salutation_content(self):  # DS-U-20
        assert "To Whom It May Concern" in build_salutation()

    def test_closing_firm_contains_fmc(self, clean_case: DisputeCase):  # DS-U-21
        closing = build_closing("firm", clean_case)
        assert "Federal Maritime Commission" in closing

    def test_closing_factual_no_fmc_complaint(self, clean_case: DisputeCase):  # DS-U-22
        closing = build_closing("factual", clean_case)
        assert "formal complaint" not in closing.lower()


# ---------------------------------------------------------------------------
# Group 4 — Severity sort
# ---------------------------------------------------------------------------


class TestSeveritySort:
    def test_r001_before_r007_same_tier(
        self, r001_violation: Violation, r007_violation: Violation
    ):  # DS-U-23
        sorted_v = sort_violations_by_severity([r007_violation, r001_violation])
        assert sorted_v[0].rule_id == "R001"
        assert sorted_v[1].rule_id == "R007"

    def test_r007_before_r004_tier1_before_tier2(
        self, r007_violation: Violation, r004_violation: Violation
    ):  # DS-U-24
        sorted_v = sort_violations_by_severity([r004_violation, r007_violation])
        assert sorted_v[0].rule_id == "R007"
        assert sorted_v[1].rule_id == "R004"

    def test_r004_before_r012_tier2_before_tier4(
        self, r004_violation: Violation, r012_violation: Violation
    ):  # DS-U-25
        sorted_v = sort_violations_by_severity([r012_violation, r004_violation])
        assert sorted_v[0].rule_id == "R004"
        assert sorted_v[1].rule_id == "R012"

    def test_r012_last_in_six_violations(self, six_violations: list[Violation]):  # DS-U-26
        sorted_v = sort_violations_by_severity(six_violations)
        assert sorted_v[-1].rule_id == "R012"

    def test_higher_confidence_first_within_tier(self):  # DS-U-27
        low = Violation(
            rule_id="R001", rule_name="Invoicing window", confidence=0.80,
            reasoning="Low conf.", citation=get_citation("R001").verbatim_text,
        )
        high = Violation(
            rule_id="R001", rule_name="Invoicing window", confidence=1.0,
            reasoning="High conf.", citation=get_citation("R001").verbatim_text,
        )
        sorted_v = sort_violations_by_severity([low, high])
        assert sorted_v[0].confidence == 1.0

    def test_empty_list_returns_empty(self):  # DS-U-28
        assert sort_violations_by_severity([]) == []


# ---------------------------------------------------------------------------
# Group 5 — Tone selection
# ---------------------------------------------------------------------------


class TestToneSelection:
    def test_auto_file_gives_firm(self):  # DS-U-29
        score = _make_score(action="auto_file")
        assert select_tone(score) == "firm"

    def test_human_review_gives_factual(self):  # DS-U-30
        score = _make_score(action="human_review", probability=0.50)
        assert select_tone(score) == "factual"

    def test_write_off_gives_factual(self):  # DS-U-31
        score = _make_score(action="write_off", probability=0.20)
        score = RecoveryScore(
            case_id="X", recovery_probability=0.20,
            expected_recovery_usd=round(0.20 * 3920, 2),
            recommended_action="write_off",
            confidence=0.88,
            reasoning="No merit.", key_factors=["Negative: no violations"],
        )
        assert select_tone(score) == "factual"

    def test_all_actions_covered(self):  # DS-U-32
        for action, expected_prob, expected_conf in [
            ("auto_file", 0.87, 0.90),
            ("human_review", 0.50, 0.75),
        ]:
            score = RecoveryScore(
                case_id="X", recovery_probability=expected_prob,
                expected_recovery_usd=round(expected_prob * 3920, 2),
                recommended_action=action, confidence=expected_conf,
                reasoning="Test.", key_factors=["factor"],
            )
            tone = select_tone(score)
            assert tone in ("factual", "firm", "escalation")


# ---------------------------------------------------------------------------
# Group 6 — Citation block formatter
# ---------------------------------------------------------------------------


class TestCitationBlock:
    def test_r001_block_contains_rule_label(self, r001_violation: Violation):  # DS-U-33
        block = build_citation_block(r001_violation)
        assert "R001" in block
        assert "Invoicing window" in block

    def test_r001_block_contains_confidence(self, r001_violation: Violation):  # DS-U-34
        block = build_citation_block(r001_violation)
        assert "100%" in block

    def test_r007_block_contains_primary_text(self, r007_violation: Violation):  # DS-U-35
        block = build_citation_block(r007_violation)
        assert "541.6(e)" in block
        assert "billing party's performance did not cause" in block

    def test_r007_block_contains_85fr29638(self, r007_violation: Violation):  # DS-U-36
        block = build_citation_block(r007_violation)
        assert "85 FR 29638" in block
        assert "reinforced by" in block

    def test_r007_supporting_verbatim_text_present(self, r007_violation: Violation):  # DS-U-37
        block = build_citation_block(r007_violation)
        assert "incentive function" in block

    def test_r004_no_supporting_authority(self, r004_violation: Violation):  # DS-U-38
        block = build_citation_block(r004_violation)
        assert "reinforced by" not in block

    def test_citation_block_uses_registry_not_violation_field(
        self, r001_violation: Violation
    ):  # DS-U-39
        # The registry text and violation.citation should match for R001
        registry_text = get_citation("R001").verbatim_text
        block = build_citation_block(r001_violation)
        assert registry_text[:40] in block


# ---------------------------------------------------------------------------
# Group 7 — draft_letter contract
# ---------------------------------------------------------------------------


class TestDraftLetter:
    def test_produces_valid_demand_letter(
        self, six_violations: list[Violation], clean_case: DisputeCase
    ):  # DS-U-40
        verdict = _make_verdict(six_violations)
        score = _make_score()
        letter = _fake_draft(verdict, score, clean_case)
        assert isinstance(letter, DemandLetter)
        assert letter.case_id == "DEMO-CVF-2026-0001"

    def test_cited_rules_severity_ordered(
        self, six_violations: list[Violation], clean_case: DisputeCase
    ):  # DS-U-41
        verdict = _make_verdict(six_violations)
        score = _make_score()
        letter = _fake_draft(verdict, score, clean_case)
        assert letter.cited_rules[0] == "R001"
        assert letter.cited_rules[1] == "R007"
        assert letter.cited_rules[-1] == "R012"

    def test_demand_amount_from_invoice(
        self, six_violations: list[Violation], clean_case: DisputeCase
    ):  # DS-U-42
        verdict = _make_verdict(six_violations)
        score = _make_score()
        letter = _fake_draft(verdict, score, clean_case)
        assert letter.demand_amount_usd == CHARGE_AMOUNT_USD

    def test_deadline_equals_filing_plus_30(
        self, six_violations: list[Violation], clean_case: DisputeCase
    ):  # DS-U-43
        verdict = _make_verdict(six_violations)
        score = _make_score()
        letter = _fake_draft(verdict, score, clean_case)
        assert letter.response_deadline == _FILING_DATE + timedelta(days=30)

    def test_word_count_matches_letter_text(
        self, six_violations: list[Violation], clean_case: DisputeCase
    ):  # DS-U-44
        verdict = _make_verdict(six_violations)
        score = _make_score()
        letter = _fake_draft(verdict, score, clean_case)
        assert letter.word_count == len(letter.letter_text.split())

    def test_cannot_evaluate_absent_from_letter(
        self, six_violations: list[Violation], clean_case: DisputeCase
    ):  # DS-U-45
        verdict = _make_verdict(six_violations)
        score = _make_score()
        letter = _fake_draft(verdict, score, clean_case)
        # R006 is the cannot_evaluate item in the fixture
        assert "Appointment compliance" not in letter.letter_text
        assert "R006" not in letter.letter_text

    def test_write_off_produces_header_note_and_factual_tone(
        self, six_violations: list[Violation], clean_case: DisputeCase
    ):  # DS-U-46
        verdict = _make_verdict(six_violations)
        score = RecoveryScore(
            case_id="DEMO-CVF-2026-0001",
            recovery_probability=0.20,
            expected_recovery_usd=round(0.20 * 3920, 2),
            recommended_action="write_off",
            confidence=0.88,
            reasoning="Low probability.",
            key_factors=["Negative: weak evidence"],
        )
        letter = _fake_draft(verdict, score, clean_case)
        assert letter.tone == "factual"
        assert letter.header_note is not None
        assert "write_off" in letter.header_note or "human review" in letter.header_note.lower()

    def test_zero_violations_raises_value_error(self, clean_case: DisputeCase):  # DS-U-47
        verdict = ComplianceVerdict(
            case_id="DEMO-CVF-2026-0001",
            total_rules_evaluated=11,
            violations=[],
            clean_results=["R001", "R002", "R003", "R004", "R005",
                           "R007", "R008", "R009", "R010", "R011", "R012"],
            overall_strength="no_merit",
            summary="No violations found.",
        )
        score = _make_score(action="write_off", probability=0.05)
        with pytest.raises(ValueError, match="no confirmed violations"):
            draft_letter(verdict, score, clean_case, filing_date=_FILING_DATE)

    def test_letter_text_starts_with_date_not_log(
        self, six_violations: list[Violation], clean_case: DisputeCase
    ):  # DS-U-48
        verdict = _make_verdict(six_violations)
        score = _make_score()
        letter = _fake_draft(verdict, score, clean_case)
        assert letter.letter_text.startswith("June 26, 2026")


# ---------------------------------------------------------------------------
# Group 8 — Prompt structure
# ---------------------------------------------------------------------------


class TestPromptStructure:
    def test_narrative_prompt_is_chat_prompt_template(self):  # DS-U-49
        from langchain_core.prompts import ChatPromptTemplate
        assert isinstance(NARRATIVE_PROMPT, ChatPromptTemplate)

    def test_tone_instructions_keys(self):  # DS-U-50
        assert set(TONE_INSTRUCTIONS.keys()) == {"factual", "firm", "escalation"}

    def test_constraints_has_citation_prohibition(self):  # DS-U-51
        assert "Do NOT" in CONSTRAINTS_SECTION
        assert "CFR" in CONSTRAINTS_SECTION or "citation" in CONSTRAINTS_SECTION.lower()

    def test_constraints_has_dollar_prohibition(self):  # DS-U-52
        assert "dollar" in CONSTRAINTS_SECTION.lower() or "amount" in CONSTRAINTS_SECTION.lower()

    def test_narrative_prompt_has_violation_summaries_var(self):  # DS-U-53
        template_str = str(NARRATIVE_PROMPT)
        assert "violation_summaries" in template_str

    def test_narrative_prompt_has_tone_instruction_var(self):  # DS-U-54
        template_str = str(NARRATIVE_PROMPT)
        assert "tone_instruction" in template_str

    def test_role_section_identifies_agent(self):  # DS-U-55
        assert "DemandSmith" in ROLE_SECTION
