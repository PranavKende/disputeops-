"""Generate synthetic PDF invoices for PaperTrail integration testing.

Produces three invoice PDFs from the existing fixture data, written to
tests/fixtures/documents/.  Each PDF mirrors the exact field values in
the corresponding DisputeCase fixture so PaperTrail's round-trip extraction
can be asserted field-by-field.

The PDFs are realistic-looking carrier invoices (letterhead, billing table,
line items, totals, tariff reference, BOL terms) but are intentionally
simple — no logos, no complex layout.  The goal is extractable text, not
print quality.

Run from the repo root:
    python tools/generate_test_documents.py

Output:
    tests/fixtures/documents/empire_freight_invoice.pdf   (clean_violation)
    tests/fixtures/documents/pacific_liner_invoice.pdf    (valid_charge)
    tests/fixtures/documents/coastal_carrier_invoice.pdf  (borderline)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fpdf import FPDF, XPos, YPos

# Ensure project root is on path when run as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from data.fixtures.borderline_case import make_borderline_case
from data.fixtures.clean_violation_case import make_clean_violation_case
from data.fixtures.valid_charge_case import make_valid_charge_case
from data.schemas.case import DisputeCase

OUTPUT_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "documents"


# ---------------------------------------------------------------------------
# Invoice PDF builder
# ---------------------------------------------------------------------------

_BLUE = (31, 73, 125)
_DARK = (40, 40, 40)
_LIGHT_GREY = (230, 230, 230)


@dataclass
class _InvoiceData:
    """Flat view of the data needed to render the PDF template."""
    carrier_name: str
    carrier_address: str
    invoice_number: str
    invoice_date: str
    bill_to: str
    bol_number: str
    container_number: str
    charge_type: str
    charge_incurred_date: str
    free_time_start: str
    free_time_end: str
    published_free_time_days: str
    tariff_number: str
    hours_billed: str
    rate_label: str
    hourly_rate: str
    total_amount: str
    basis_for_charge: str
    bol_terms: str


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "N/A"
    return dt.strftime("%Y-%m-%d")


def _extract_data(case: DisputeCase, carrier_address: str, bol_terms: str) -> _InvoiceData:
    inv = case.invoice
    ev = case.evidence
    rate_label = "Per Day Rate" if inv.billed_unit_inferred == "daily" else "Per Hour Rate"
    hours_label = (
        f"{inv.hours_billed / 24:.2f} days ({inv.hours_billed:.1f} hours)"
        if inv.billed_unit_inferred == "daily"
        else f"{inv.hours_billed:.1f} hours"
    )
    pub_free = (
        f"{ev.published_free_time_days:.0f} days"
        if ev.published_free_time_days is not None
        else "N/A"
    )
    tariff = (
        ev.carrier_tariff_reference.tariff_number
        if ev.carrier_tariff_reference
        else "N/A"
    )
    return _InvoiceData(
        carrier_name=inv.carrier_name,
        carrier_address=carrier_address,
        invoice_number=inv.invoice_number,
        invoice_date=str(inv.invoice_date),
        bill_to=inv.billed_to_party,
        bol_number=inv.bol_number or "N/A",
        container_number=inv.container_number or "N/A",
        charge_type=inv.charge_type.upper(),
        charge_incurred_date=str(inv.charge_incurred_date),
        free_time_start=_fmt_dt(inv.free_time_start),
        free_time_end=_fmt_dt(inv.free_time_end),
        published_free_time_days=pub_free,
        tariff_number=tariff,
        hours_billed=hours_label,
        rate_label=rate_label,
        hourly_rate=f"${inv.hourly_rate_usd:,.2f}",
        total_amount=f"${inv.total_amount_usd:,.2f}",
        basis_for_charge=inv.basis_for_charge or "See tariff.",
        bol_terms=bol_terms,
    )


def _build_pdf(data: _InvoiceData) -> bytes:
    """Render a single-page invoice PDF and return the raw bytes."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(20, 20, 20)
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    NL = {"new_x": XPos.LMARGIN, "new_y": YPos.NEXT}

    # ---- Carrier letterhead ----
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*_BLUE)
    pdf.cell(0, 10, data.carrier_name, **NL)

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_DARK)
    pdf.cell(0, 5, data.carrier_address, **NL)
    pdf.ln(4)

    # ---- Invoice header bar ----
    pdf.set_fill_color(*_BLUE)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "DEMURRAGE / DETENTION INVOICE", fill=True, align="C", **NL)
    pdf.set_text_color(*_DARK)
    pdf.ln(3)

    # ---- Invoice meta: two-column layout ----
    def _row(label: str, value: str, bold_value: bool = False) -> None:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(55, 6, label + ":", border=0)
        pdf.set_font("Helvetica", "B" if bold_value else "", 9)
        pdf.cell(0, 6, value, **NL)

    _row("Invoice No.", data.invoice_number, bold_value=True)
    _row("Invoice Date", data.invoice_date)
    _row("Bill To", data.bill_to)
    _row("BOL Number", data.bol_number)
    _row("Container Number", data.container_number)
    pdf.ln(3)

    # ---- Charge details section ----
    pdf.set_fill_color(*_LIGHT_GREY)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, "Charge Details", fill=True, **NL)
    pdf.ln(1)

    _row("Charge Type", data.charge_type)
    _row("Charge Incurred Date", data.charge_incurred_date)
    _row("Free Time Start", data.free_time_start)
    _row("Free Time End", data.free_time_end)
    _row("Published Free Time", data.published_free_time_days)
    _row("Tariff Reference", f"tariff {data.tariff_number}")
    pdf.ln(3)

    # ---- Billing line items table ----
    pdf.set_fill_color(*_LIGHT_GREY)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, "Billing Summary", fill=True, **NL)
    pdf.ln(1)

    col_w = [90, 45, 45]
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(*_BLUE)
    pdf.set_text_color(255, 255, 255)
    for header, w in zip(["Description", data.rate_label, "Amount"], col_w):
        pdf.cell(w, 7, header, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_text_color(*_DARK)
    pdf.set_font("Helvetica", "", 9)
    # Rate cell includes /hr or /day suffix so regex can extract it without the LLM
    rate_unit_suffix = "/day" if "Per Day" in data.rate_label else "/hr"
    pdf.cell(col_w[0], 6, data.charge_type + " -- " + data.hours_billed, border=1)
    pdf.cell(col_w[1], 6, data.hourly_rate + rate_unit_suffix, border=1, align="R")
    pdf.cell(col_w[2], 6, data.total_amount, border=1, align="R")
    pdf.ln()

    # Total row
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(*_LIGHT_GREY)
    pdf.cell(col_w[0] + col_w[1], 8, "Total Amount Due", border=1, fill=True, align="R")
    pdf.cell(col_w[2], 8, data.total_amount, border=1, align="R", fill=True)
    pdf.ln(5)

    # ---- Basis for charge ----
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(*_LIGHT_GREY)
    pdf.cell(0, 7, "Basis for Charge", fill=True, **NL)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 5, data.basis_for_charge)
    pdf.ln(3)

    # ---- BOL Terms (page 1 condensed) ----
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(*_LIGHT_GREY)
    pdf.cell(0, 7, "Bill of Lading Terms", fill=True, **NL)
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(0, 4, data.bol_terms)

    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# Per-fixture generation
# ---------------------------------------------------------------------------

def _generate_clean_violation() -> tuple[str, bytes]:
    case = make_clean_violation_case()
    data = _extract_data(
        case,
        carrier_address="1 Maritime Plaza, San Francisco, CA 94111  |  FMC No. 001234",
        bol_terms=(
            "Shipper: Pacifica Apparel Inc.  Carrier: Empire Freight Lines.  "
            "Port of discharge: Long Beach, CA.  Container: MEDU9871234.  "
            "Terms: CY/CY.  Notify: Pacifica Apparel Inc., Los Angeles, CA."
        ),
    )
    return "empire_freight_invoice.pdf", _build_pdf(data)


def _generate_valid_charge() -> tuple[str, bytes]:
    case = make_valid_charge_case()
    data = _extract_data(
        case,
        carrier_address="400 Container Way, Oakland, CA 94607  |  FMC No. 005678",
        bol_terms=(
            "Shipper: Westcoast Distribution LLC.  Carrier: Pacific Liner Co.  "
            "Port of discharge: Oakland, CA.  Container: TCLU4470129.  "
            "Terms: CY/CY.  Notify: Westcoast Distribution LLC, Seattle, WA."
        ),
    )
    return "pacific_liner_invoice.pdf", _build_pdf(data)


def _generate_borderline() -> tuple[str, bytes]:
    case = make_borderline_case()
    data = _extract_data(
        case,
        carrier_address="200 Port Commerce Dr, Savannah, GA 31401  |  FMC No. 009012",
        bol_terms=(
            "Shipper: Heartland Imports LLC.  Carrier: Coastal Carrier Group.  "
            "Port of discharge: Savannah, GA.  Container: CMAU7723445.  "
            "Terms: CY/CY.  Notify: Heartland Imports LLC, Chicago, IL."
        ),
    )
    return "coastal_carrier_invoice.pdf", _build_pdf(data)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    generators = [
        _generate_clean_violation,
        _generate_valid_charge,
        _generate_borderline,
    ]

    for gen in generators:
        filename, pdf_bytes = gen()
        out_path = OUTPUT_DIR / filename
        out_path.write_bytes(pdf_bytes)
        print(f"  Written: {out_path}  ({len(pdf_bytes):,} bytes)")

    print(f"\nAll {len(generators)} PDFs written to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
