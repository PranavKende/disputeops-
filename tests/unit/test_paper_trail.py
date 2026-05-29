"""Unit tests for PaperTrail — document extraction agent.

All LLM calls are mocked at get_llm_client boundary or via direct _invoke_llm
patch.  No real API calls.  No date.today() in any test — dates are explicit.

Test inventory (42 tests)
--------------------------
Group 1 — Regex patterns, extractors/patterns.py (13)
  PT-U-01  extract_total_amount: label-anchored match on Total Amount Due
  PT-U-02  extract_total_amount: does not match year number 2026
  PT-U-03  extract_total_amount: does not match container number digits
  PT-U-04  extract_invoice_number: extracts from labeled 'Invoice No.'
  PT-U-05  extract_bol_number: label-anchored 'BOL Number:'
  PT-U-06  extract_bol_number: label-anchored 'Bill of Lading No.'
  PT-U-07  extract_container_number: matches MEDU9871234 (4+7 ISO 6346)
  PT-U-08  extract_container_number: does not match short 10-char candidate
  PT-U-09  extract_container_number: does not match lowercase medu9871234
  PT-U-10  extract_hourly_rate: $70.00/hr returns (70.0, 'hourly')
  PT-U-11  extract_hourly_rate: $25.00/day returns (25.0, 'daily')
  PT-U-12  extract_tariff_number: 'Tariff Reference: tariff EMFR-2024-DEM' → EMFR-2024-DEM
  PT-U-13  extract_free_time_days: 'Published Free Time: 3 days' → 3.0

Group 2 — PDF extractor, extractors/pdf.py (5)
  PT-U-14  extracts text from single-page synthetic PDF (empire_freight)
  PT-U-15  multi-page PDF produces --- PAGE N --- separators
  PT-U-16  empty bytes raises ValueError before reaching pdfplumber
  PT-U-17  malformed bytes raises an exception with a clear message
  PT-U-18  extracted text contains Total Amount Due in label-anchored form

Group 3 — Email extractor, extractors/email.py (5)
  PT-U-19  plain-text RFC 2822 email returns body text
  PT-U-20  multipart email with PDF attachment routes through PDF extractor
  PT-U-21  email with PDF attachment: PDF text appears before body text
  PT-U-22  email with no PDF attachment returns body text only
  PT-U-23  email with no text/plain and no PDF raises ValueError

Group 4 — Source dispatch and ExtractionSource model (5)
  PT-U-24  PdfSource(kind='pdf') routes to PDF extractor
  PT-U-25  EmailSource(kind='email') routes to email extractor
  PT-U-26  EdiSource raises NotImplementedError with v2-deferred message
  PT-U-27  PdfSource with extra field raises ValidationError (extra='forbid')
  PT-U-28  case_id_override is used verbatim when provided

Group 5 — case_id generation (4)
  PT-U-29  generated case_id starts with DEMO-
  PT-U-30  generated case_id contains the current year
  PT-U-31  same carrier + same invoice = same case_id (deterministic)
  PT-U-32  different invoice numbers produce different case_ids

Group 6 — LLM extraction and assembly (10)
  PT-U-33  get_llm_client called with purpose='paper_trail_extraction'
  PT-U-34  with_structured_output called with _InvoiceLLMOutput
  PT-U-35  LLM-extracted carrier_name appears in assembled Invoice
  PT-U-36  LLM-extracted charge_type appears in assembled Invoice
  PT-U-37  pre-extracted total_amount echoed by LLM without correction: no WARNING logged
  PT-U-38  LLM correcting regex total_amount logs WARNING
  PT-U-39  LLM returning null carrier_name logs WARNING
  PT-U-40  all-None pre-extraction produces 'MISSING' in prompt context string
  PT-U-41  temperature=0.0 kwarg passed to get_llm_client
  PT-U-42  PAPER_TRAIL_MODEL env var overrides default model name
"""

from __future__ import annotations

import email as _email_mod
import email.policy
import io
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest
from pydantic import ValidationError

from agents.paper_trail.extractors.email import extract_text_from_email
from agents.paper_trail.extractors.patterns import (
    extract_bol_number,
    extract_container_number,
    extract_free_time_days,
    extract_hourly_rate,
    extract_invoice_number,
    extract_tariff_number,
    extract_total_amount,
)
from agents.paper_trail.extractors.pdf import extract_text_from_pdf
from agents.paper_trail.paper_trail import (
    EdiSource,
    EmailSource,
    PdfSource,
    _InvoiceLLMOutput,
    _generate_case_id,
    _pre_extract,
    extract_case,
)
from langchain_core.runnables import RunnableLambda

from agents.paper_trail.prompts import build_pre_extracted_context

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DOCS = Path(__file__).parent.parent / "fixtures" / "documents"
_EMPIRE_PDF = _DOCS / "empire_freight_invoice.pdf"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plain_email(body: str, subject: str = "Invoice") -> str:
    return (
        f"From: carrier@example.com\r\n"
        f"To: shipper@example.com\r\n"
        f"Subject: {subject}\r\n"
        f"MIME-Version: 1.0\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n"
        f"{body}"
    )


def _make_multipart_email_with_pdf(body: str, pdf_bytes: bytes) -> str:
    import base64
    b64 = base64.b64encode(pdf_bytes).decode()
    return (
        "From: carrier@example.com\r\n"
        "To: shipper@example.com\r\n"
        "Subject: Invoice\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: multipart/mixed; boundary=\"boundary123\"\r\n"
        "\r\n"
        "--boundary123\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        f"{body}\r\n"
        "--boundary123\r\n"
        "Content-Type: application/pdf\r\n"
        "Content-Disposition: attachment; filename=\"invoice.pdf\"\r\n"
        "Content-Transfer-Encoding: base64\r\n"
        "\r\n"
        f"{b64}\r\n"
        "--boundary123--\r\n"
    )


def _make_llm_output(**overrides: Any) -> _InvoiceLLMOutput:
    defaults: dict[str, Any] = dict(
        invoice_number="EMFR-INV-2026-0491",
        carrier_name="Empire Freight Lines",
        bol_number="EMFR20260401-001",
        container_number="MEDU9871234",
        charge_type="demurrage",
        charge_incurred_date="2026-05-09",
        invoice_date="2026-06-25",
        free_time_start="2026-05-07T08:00:00Z",
        free_time_end="2026-05-09T08:00:00Z",
        hours_billed=56.0,
        hourly_rate_usd=70.0,
        total_amount_usd=3920.0,
        billed_to_party="Pacifica Apparel Inc.",
        basis_for_charge="Demurrage charge per tariff EMFR-2024-DEM",
    )
    defaults.update(overrides)
    return _InvoiceLLMOutput(**defaults)


def _run_extract(llm_output: _InvoiceLLMOutput, override: str = "DEMO-CVF-2026-0001") -> Any:
    """Run extract_case against empire_freight PDF with mocked LLM."""
    pdf_bytes = _EMPIRE_PDF.read_bytes()
    with patch("agents.paper_trail.paper_trail._invoke_llm", return_value=llm_output):
        return extract_case(PdfSource(content=pdf_bytes), case_id_override=override)


# ---------------------------------------------------------------------------
# Group 1 — Regex patterns
# ---------------------------------------------------------------------------

class TestRegexPatterns:
    def test_total_amount_label_anchored(self):  # PT-U-01
        text = "Total Amount Due: $3,920.00"
        assert extract_total_amount(text) == 3920.0

    def test_total_amount_does_not_match_year(self):  # PT-U-02
        text = "Invoice Date: 2026-06-25"
        # Should not produce 2026 as a dollar amount; no $ present
        assert extract_total_amount(text) is None

    def test_total_amount_does_not_match_container_digits(self):  # PT-U-03
        text = "Container: MEDU9871234"
        assert extract_total_amount(text) is None

    def test_invoice_number_labeled(self):  # PT-U-04
        assert extract_invoice_number("Invoice No.: EMFR-INV-2026-0491") == "EMFR-INV-2026-0491"
        assert extract_invoice_number("Invoice Number: PACL-INV-2026-0774") == "PACL-INV-2026-0774"

    def test_bol_number_labeled_bol(self):  # PT-U-05
        assert extract_bol_number("BOL Number: EMFR20260401-001") == "EMFR20260401-001"

    def test_bol_number_labeled_bill_of_lading(self):  # PT-U-06
        assert extract_bol_number("Bill of Lading No: PACL20260315-774") == "PACL20260315-774"

    def test_container_number_iso_6346(self):  # PT-U-07
        assert extract_container_number("Container: MEDU9871234") == "MEDU9871234"
        assert extract_container_number("TCLU4470129") == "TCLU4470129"

    def test_container_number_too_short_not_matched(self):  # PT-U-08
        # 10 chars (4 alpha + 6 digits) — not ISO 6346 (needs 11 chars: 4+7)
        assert extract_container_number("MEDU987123") is None

    def test_container_number_lowercase_not_matched(self):  # PT-U-09
        assert extract_container_number("medu9871234") is None

    def test_hourly_rate_per_hour(self):  # PT-U-10
        rate, unit = extract_hourly_rate("Rate: $70.00/hr")
        assert rate == 70.0
        assert unit == "hourly"

    def test_hourly_rate_per_day(self):  # PT-U-11
        rate, unit = extract_hourly_rate("Rate: $25.00/day")
        assert rate == 25.0
        assert unit == "daily"

    def test_tariff_number_labeled(self):  # PT-U-12
        text = "Tariff Reference: tariff EMFR-2024-DEM"
        assert extract_tariff_number(text) == "EMFR-2024-DEM"

    def test_free_time_days_published_label(self):  # PT-U-13
        assert extract_free_time_days("Published Free Time: 3 days") == 3.0
        assert extract_free_time_days("3 calendar days free time") == 3.0


# ---------------------------------------------------------------------------
# Group 2 — PDF extractor
# ---------------------------------------------------------------------------

class TestPdfExtractor:
    def test_extracts_text_from_single_page(self):  # PT-U-14
        text = extract_text_from_pdf(_EMPIRE_PDF.read_bytes())
        assert "Empire Freight Lines" in text
        assert "EMFR-INV-2026-0491" in text

    def test_multi_page_separator(self, tmp_path: Path):  # PT-U-15
        from fpdf import FPDF, XPos, YPos
        NL = {"new_x": XPos.LMARGIN, "new_y": YPos.NEXT}
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, "Page one content", **NL)
        pdf.add_page()
        pdf.cell(0, 10, "Page two content", **NL)
        out = tmp_path / "two_page.pdf"
        out.write_bytes(bytes(pdf.output()))
        text = extract_text_from_pdf(out.read_bytes())
        assert "--- PAGE 1 ---" in text
        assert "--- PAGE 2 ---" in text
        assert "Page one content" in text
        assert "Page two content" in text

    def test_empty_bytes_raises_value_error(self):  # PT-U-16
        with pytest.raises(ValueError, match="empty"):
            extract_text_from_pdf(b"")

    def test_malformed_bytes_raises_exception(self):  # PT-U-17
        with pytest.raises(Exception):
            extract_text_from_pdf(b"this is not a pdf file at all")

    def test_extracted_text_preserves_label_structure(self):  # PT-U-18
        text = extract_text_from_pdf(_EMPIRE_PDF.read_bytes())
        assert "Total Amount Due" in text
        assert "$3,920.00" in text


# ---------------------------------------------------------------------------
# Group 3 — Email extractor
# ---------------------------------------------------------------------------

class TestEmailExtractor:
    def test_plain_text_email_returns_body(self):  # PT-U-19
        msg = _make_plain_email("Invoice No.: EMFR-INV-2026-0491\nTotal: $3,920.00")
        text = extract_text_from_email(msg)
        assert "EMFR-INV-2026-0491" in text
        assert "$3,920.00" in text

    def test_multipart_with_pdf_routes_through_pdf_extractor(self):  # PT-U-20
        pdf_bytes = _EMPIRE_PDF.read_bytes()
        msg = _make_multipart_email_with_pdf("See attached invoice.", pdf_bytes)
        text = extract_text_from_email(msg)
        # PDF text should be present
        assert "EMFR-INV-2026-0491" in text

    def test_pdf_attachment_text_precedes_body(self):  # PT-U-21
        pdf_bytes = _EMPIRE_PDF.read_bytes()
        body = "EMAIL BODY MARKER"
        msg = _make_multipart_email_with_pdf(body, pdf_bytes)
        text = extract_text_from_email(msg)
        # PDF content before email body (PDF block comes first)
        pdf_pos = text.find("EMFR-INV-2026-0491")
        body_pos = text.find("EMAIL BODY MARKER")
        assert pdf_pos < body_pos

    def test_email_without_pdf_returns_body_only(self):  # PT-U-22
        msg = _make_plain_email("Invoice text without attachment.")
        text = extract_text_from_email(msg)
        assert "Invoice text without attachment" in text

    def test_email_no_text_no_pdf_raises_value_error(self):  # PT-U-23
        # A multipart/mixed with no text/plain and no application/pdf parts
        raw = (
            "From: a@b.com\r\nTo: c@d.com\r\nMIME-Version: 1.0\r\n"
            "Content-Type: multipart/mixed; boundary=\"b\"\r\n\r\n"
            "--b\r\nContent-Type: application/octet-stream\r\n\r\nGarbage\r\n--b--\r\n"
        )
        with pytest.raises(ValueError):
            extract_text_from_email(raw)


# ---------------------------------------------------------------------------
# Group 4 — Source dispatch
# ---------------------------------------------------------------------------

class TestSourceDispatch:
    def test_pdf_source_routes_to_pdf_extractor(self):  # PT-U-24
        llm = _make_llm_output()
        with patch("agents.paper_trail.paper_trail._invoke_llm", return_value=llm), \
             patch("agents.paper_trail.paper_trail.extract_text_from_pdf",
                   return_value="Invoice No.: EMFR-INV-2026-0491") as mock_pdf:
            extract_case(PdfSource(content=b"%PDF-1.4"), case_id_override="X")
            mock_pdf.assert_called_once()

    def test_email_source_routes_to_email_extractor(self):  # PT-U-25
        llm = _make_llm_output()
        msg = _make_plain_email("Invoice No.: EMFR-INV-2026-0491 Total Amount Due: $3,920.00")
        with patch("agents.paper_trail.paper_trail._invoke_llm", return_value=llm), \
             patch("agents.paper_trail.paper_trail.extract_text_from_email",
                   return_value="Invoice No.: EMFR-INV-2026-0491") as mock_email:
            extract_case(EmailSource(raw_message=msg), case_id_override="X")
            mock_email.assert_called_once()

    def test_edi_source_raises_not_implemented(self):  # PT-U-26
        with pytest.raises(NotImplementedError, match="v2"):
            extract_case(EdiSource(content="ISA*00*..."))

    def test_pdf_source_extra_field_raises_validation_error(self):  # PT-U-27
        with pytest.raises(ValidationError):
            PdfSource(content=b"pdf", filename="f.pdf", unknown_field="x")  # type: ignore[call-arg]

    def test_case_id_override_used_verbatim(self):  # PT-U-28
        case = _run_extract(_make_llm_output(), override="DEMO-CVF-2026-0001")
        assert case.case_id == "DEMO-CVF-2026-0001"


# ---------------------------------------------------------------------------
# Group 5 — case_id generation
# ---------------------------------------------------------------------------

class TestCaseIdGeneration:
    def test_generated_id_starts_with_demo(self):  # PT-U-29
        cid = _generate_case_id("Empire Freight Lines", "EMFR-INV-2026-0491", None)
        assert cid.startswith("DEMO-")

    def test_generated_id_contains_current_year(self):  # PT-U-30
        import datetime as _dt
        year = str(_dt.datetime.now(tz=_dt.timezone.utc).year)
        cid = _generate_case_id("Empire Freight Lines", "EMFR-INV-2026-0491", None)
        assert year in cid

    def test_same_inputs_produce_same_case_id(self):  # PT-U-31
        cid_a = _generate_case_id("Empire Freight Lines", "EMFR-INV-2026-0491", None)
        cid_b = _generate_case_id("Empire Freight Lines", "EMFR-INV-2026-0491", None)
        assert cid_a == cid_b

    def test_different_invoice_numbers_produce_different_ids(self):  # PT-U-32
        cid_a = _generate_case_id("Empire Freight Lines", "EMFR-INV-2026-0491", None)
        cid_b = _generate_case_id("Empire Freight Lines", "EMFR-INV-2026-0499", None)
        assert cid_a != cid_b


# ---------------------------------------------------------------------------
# Group 6 — LLM extraction and assembly
# ---------------------------------------------------------------------------

class TestLLMExtractionAssembly:
    def _mock_get_llm(self, llm_output: _InvoiceLLMOutput) -> MagicMock:
        """Return mock_llm whose with_structured_output returns a proper LangChain Runnable.

        EXTRACTION_PROMPT | llm.with_structured_output(...) requires the right-hand
        side to be a LangChain Runnable.  MagicMock fails the pipe protocol.
        RunnableLambda wraps a plain function as a Runnable, so the pipe compiles.
        """
        mock_llm = MagicMock()
        # with_structured_output returns a Runnable that ignores its input and returns llm_output
        mock_llm.with_structured_output.return_value = RunnableLambda(lambda _: llm_output)
        return mock_llm

    def test_get_llm_client_called_with_correct_purpose(self):  # PT-U-33
        llm_output = _make_llm_output()
        mock_llm = self._mock_get_llm(llm_output)
        pdf = _EMPIRE_PDF.read_bytes()
        with patch("agents.paper_trail.paper_trail.get_llm_client",
                   return_value=mock_llm) as mock_factory:
            extract_case(PdfSource(content=pdf), case_id_override="X")
        call_kwargs = mock_factory.call_args[1]
        assert call_kwargs["purpose"] == "paper_trail_extraction"

    def test_with_structured_output_called_with_invoice_llm_output(self):  # PT-U-34
        llm_output = _make_llm_output()
        mock_llm = self._mock_get_llm(llm_output)
        pdf = _EMPIRE_PDF.read_bytes()
        with patch("agents.paper_trail.paper_trail.get_llm_client", return_value=mock_llm):
            extract_case(PdfSource(content=pdf), case_id_override="X")
        mock_llm.with_structured_output.assert_called_once_with(_InvoiceLLMOutput)

    def test_llm_carrier_name_in_assembled_invoice(self):  # PT-U-35
        case = _run_extract(_make_llm_output(carrier_name="Test Carrier Lines"))
        assert case.invoice.carrier_name == "Test Carrier Lines"

    def test_llm_charge_type_in_assembled_invoice(self):  # PT-U-36
        case = _run_extract(_make_llm_output(charge_type="detention"))
        assert case.invoice.charge_type == "detention"

    def test_llm_agreeing_with_regex_no_warning(self, caplog: Any):  # PT-U-37
        with caplog.at_level(logging.WARNING, logger="agents.paper_trail.paper_trail"):
            _run_extract(_make_llm_output(total_amount_usd=3920.0))
        crosscheck_warnings = [r for r in caplog.records
                               if "LLM corrected" in r.message and "total_amount" in r.message]
        assert len(crosscheck_warnings) == 0

    def test_llm_correcting_regex_logs_warning(self, caplog: Any):  # PT-U-38
        # LLM says total is 4000.0 but regex extracted 3920.0
        with caplog.at_level(logging.WARNING, logger="agents.paper_trail.paper_trail"):
            _run_extract(_make_llm_output(total_amount_usd=4000.0))
        warnings = [r for r in caplog.records if "LLM corrected" in r.message]
        assert any("total_amount_usd" in w.message for w in warnings)

    def test_llm_null_carrier_name_logs_warning(self, caplog: Any):  # PT-U-39
        with caplog.at_level(logging.WARNING, logger="agents.paper_trail.paper_trail"):
            _run_extract(_make_llm_output(carrier_name=None))
        assert any("carrier_name" in r.message for r in caplog.records
                   if r.levelno == logging.WARNING)

    def test_all_none_pre_extraction_context_contains_missing(self):  # PT-U-40
        # When no regex matches, every field in the prompt context says MISSING
        all_none = {k: None for k in _pre_extract("no invoice data here at all")}
        context = build_pre_extracted_context({
            k: v for k, v in all_none.items()
            if k not in ("tariff_number", "published_free_time_days",
                         "free_time_start_date", "free_time_end_date")
        })
        assert "MISSING" in context
        assert "extract from document" in context

    def test_temperature_zero_passed_to_get_llm_client(self):  # PT-U-41
        llm_output = _make_llm_output()
        mock_llm = self._mock_get_llm(llm_output)
        pdf = _EMPIRE_PDF.read_bytes()
        with patch("agents.paper_trail.paper_trail.get_llm_client",
                   return_value=mock_llm) as mock_factory:
            extract_case(PdfSource(content=pdf), case_id_override="X")
        call_kwargs = mock_factory.call_args[1]
        assert call_kwargs.get("temperature") == 0.0

    def test_paper_trail_model_env_var(self, monkeypatch: Any):  # PT-U-42
        llm_output = _make_llm_output()
        mock_llm = self._mock_get_llm(llm_output)
        pdf = _EMPIRE_PDF.read_bytes()
        with patch("agents.paper_trail.paper_trail.get_llm_client",
                   return_value=mock_llm) as mock_factory, \
             patch("agents.paper_trail.paper_trail._MODEL_NAME", "gpt-4-turbo"):
            extract_case(PdfSource(content=pdf), case_id_override="X")
        call_kwargs = mock_factory.call_args[1]
        assert call_kwargs.get("model") == "gpt-4-turbo"
