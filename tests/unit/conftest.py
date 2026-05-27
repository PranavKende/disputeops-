"""Shared test fixtures and case-builder helpers for ComplianceAuditor unit tests.

All builders accept keyword overrides so individual test cases can mutate
only the fields that matter for that scenario without repeating boilerplate.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from data.schemas.case import DisputeCase, EvidencePackage, Invoice, TariffReference, TerminalRecord
from data.schemas.verdict import RuleResult


# ---------------------------------------------------------------------------
# Invoice builder
# ---------------------------------------------------------------------------

def make_invoice(**overrides) -> Invoice:
    """Return a valid ``Invoice`` with sensible defaults.

    Defaults represent a straightforward 8-hour detention invoice issued
    10 days after the charge was incurred — compliant on all deterministic
    rules unless overrides say otherwise.
    """
    base_date = date(2024, 6, 1)
    defaults = dict(
        invoice_number="INV-TEST-001",
        carrier_name="Atlas Shipping Lines",
        bol_number="BOLT123456",
        container_number="ATSU1234567",
        charge_type="detention",
        charge_incurred_date=base_date,
        invoice_date=base_date + timedelta(days=10),
        free_time_start=datetime(2024, 5, 30, 8, 0, tzinfo=timezone.utc),
        free_time_end=datetime(2024, 6, 1, 8, 0, tzinfo=timezone.utc),  # ends on charge date
        hours_billed=8.0,
        hourly_rate_usd=75.0,
        total_amount_usd=600.0,   # 8 × 75 = 600 — correct
        billed_to_party="ACME Imports LLC",
        basis_for_charge=(
            "Container held beyond free time. "
            "Contact disputes@atlasshipping.com or visit https://atlasshipping.com/disputes"
        ),
    )
    defaults.update(overrides)
    return Invoice(**defaults)


# ---------------------------------------------------------------------------
# EvidencePackage builder
# ---------------------------------------------------------------------------

def make_evidence(**overrides) -> EvidencePackage:
    """Return an ``EvidencePackage`` with sensible defaults."""
    defaults = dict(
        gate_in_timestamp=datetime(2024, 5, 30, 7, 50, tzinfo=timezone.utc),
        gate_out_timestamp=datetime(2024, 6, 1, 9, 15, tzinfo=timezone.utc),
        appointment_time=datetime(2024, 5, 30, 8, 0, tzinfo=timezone.utc),
        shipper_name="ACME Imports LLC",
        bol_terms=(
            "Shipper: ACME Imports LLC. "
            "Carrier: Atlas Shipping Lines. "
            "Free time: 2 days. "
            "Disputes: contact disputes@atlasshipping.com"
        ),
        carrier_tariff_reference=TariffReference(
            tariff_number="ATLAS-001",
            effective_date=date(2024, 1, 1),
            published_free_time_hours=48.0,
        ),
        published_free_time_days=2.0,
        dispute_notice_date=None,
    )
    defaults.update(overrides)
    return EvidencePackage(**defaults)


# ---------------------------------------------------------------------------
# DisputeCase builder
# ---------------------------------------------------------------------------

def make_case(
    case_id: str = "CASE-TEST-001",
    invoice_overrides: dict | None = None,
    evidence_overrides: dict | None = None,
) -> DisputeCase:
    """Return a ``DisputeCase`` with independently overridable sub-models.

    Args:
        case_id: Case identifier string.
        invoice_overrides: Kwargs forwarded to ``make_invoice()``.
        evidence_overrides: Kwargs forwarded to ``make_evidence()``.
    """
    return DisputeCase(
        case_id=case_id,
        invoice=make_invoice(**(invoice_overrides or {})),
        evidence=make_evidence(**(evidence_overrides or {})),
        created_at=datetime(2024, 6, 11, 0, 0, tzinfo=timezone.utc),
    )
