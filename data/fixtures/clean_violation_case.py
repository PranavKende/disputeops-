"""Demo fixture: Empire Freight Lines / Pacifica Apparel Inc. — clean strong violation case.

SCENARIO
--------
Empire Freight Lines (NVOCC) billed Pacifica Apparel Inc. $3,920.00 in
demurrage on container MEDU9871234 at the Port of Long Beach, California.
BOL EMFR20260401-001.

VIOLATIONS THAT FIRE (6 total, overall_strength = "strong")
-----------------------------------------------------------
R001 (conf=1.0, deterministic)
    Invoice issued June 25, 2026 — exactly 47 days after the charge was
    incurred on May 9, 2026, exceeding the 30-day §541.7(a) window.

R002 (conf=0.75, LLM fallback)
    basis_for_charge is "Demurrage charge per tariff EMFR-2024-DEM" — a generic
    phrase in R002's blocklist.  LLM determines this fails §541.6(a)(4)'s
    requirement to explain why Pacifica Apparel Inc. is the liable party.

R004 (conf=1.0, deterministic)
    TariffReference EMFR-2024-DEM publishes 72 hours free time; the invoice
    claims only 48 hours (free_time_end minus free_time_start).  24-hour
    shortfall is well outside the 0.5-hour tolerance.

R007 (conf=0.92, LLM)
    ILWU 48-hour work stoppage, May 8–9, 2026, overlapped the invoice's
    billed window (May 7–9, 2026).  Port of Long Beach terminal operations
    were suspended by ILWU Local 13 job action.  LLM applies the 85 FR 29638
    incentive-function test: retrieval was impossible during the stoppage;
    §541.6(e)(2) certification is false.

R011 (conf=1.0, deterministic)
    charge_type = "demurrage"; hours_billed = 56.0 (2.33 days — not a clean
    multiple of 24).  Demurrage must be billed per calendar day per
    §541.6(c); per-hour billing on a demurrage charge violates this rule.

R012 (conf=1.0, deterministic)
    basis_for_charge contains no URL and no dispute-contact marker.
    §541.6(d) dispute mechanism disclosure is absent.

RULES THAT PASS (5)
-------------------
R003  billed_to_party = "Pacifica Apparel Inc." = shipper_name — exact match.
R005  gate_in delta = 4 min (< 15-min tolerance); gate_out delta = 3 min.
R008  Dispute window status reporter — always violated=False.
R009  56h × $70.00/h = $3,920.00 — math correct within $0.01 tolerance.
R010  charge_incurred_date (May 9) == free_time_end.date() (May 9) — billing
      started on the day free time expired; not before.

RULES THAT CANNOT EVALUATE (1)
-------------------------------
R006  No appointment_time and no appointment_scheduled terminal records.

LLM MOCK REQUIREMENTS
----------------------
  "R002_basis_quality": violated=True,  confidence=0.75
  "R007_force_majeure": violated=True,  confidence=0.92
  All other LLM-routed purposes default to violated=False, confidence=0.85.

DATE ARITHMETIC
---------------
  free_time_start   = 2026-05-07 08:00 UTC
  free_time_end     = 2026-05-09 08:00 UTC   (48h claimed; tariff says 72h → R004)
  charge_incurred   = 2026-05-09             (same date as free_time_end → R010 passes)
  invoice_date      = 2026-06-25             (47 days after charge_incurred → R001)
  ILWU stoppage     = 2026-05-08 00:00 – 2026-05-10 00:00 UTC (overlaps window)
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from data.schemas.case import (
    DisputeCase,
    EvidencePackage,
    Invoice,
    TariffReference,
)

# ---------------------------------------------------------------------------
# Named constants — referenced in integration test assertions
# ---------------------------------------------------------------------------

CARRIER_NAME:      str   = "Empire Freight Lines"
BILLED_PARTY:      str   = "Pacifica Apparel Inc."
CONTAINER_NUMBER:  str   = "MEDU9871234"
CHARGE_AMOUNT_USD: float = 3920.00
BOL_NUMBER:        str   = "EMFR20260401-001"

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_clean_violation_case() -> DisputeCase:
    """Return the Empire Freight Lines / Pacifica Apparel strong-violation case.

    Produces 6 violations (R001, R002, R004, R007, R011, R012), overall_strength
    = 'strong', and 1 cannot_evaluate entry (R006).

    Note on hours_billed: 56.0 hours (2.33 days) is intentionally not a clean
    multiple of 24, which causes R011 (per-day billing rule) to fire for
    demurrage charges.  This is consistent with the scenario — Empire Freight
    Lines billed hourly rather than per calendar day.
    """
    invoice = Invoice(
        invoice_number="EMFR-INV-2026-0491",
        carrier_name=CARRIER_NAME,
        bol_number=BOL_NUMBER,
        container_number=CONTAINER_NUMBER,
        charge_type="demurrage",
        charge_incurred_date=date(2026, 5, 9),
        invoice_date=date(2026, 6, 25),               # 47 days → R001 violation
        free_time_start=datetime(2026, 5, 7, 8, 0, tzinfo=timezone.utc),
        free_time_end=datetime(2026, 5, 9, 8, 0, tzinfo=timezone.utc),   # 48h claimed
        hours_billed=56.0,                             # 2.33 days → R011 violation
        hourly_rate_usd=70.0,
        total_amount_usd=CHARGE_AMOUNT_USD,            # 56 × 70 = 3920 → R009 passes
        billed_to_party=BILLED_PARTY,
        basis_for_charge="Demurrage charge per tariff EMFR-2024-DEM",  # generic → R002 LLM
    )

    evidence = EvidencePackage(
        gate_in_timestamp=datetime(2026, 5, 7, 7, 56, tzinfo=timezone.utc),   # 4 min before
        gate_out_timestamp=datetime(2026, 5, 9, 8, 3, tzinfo=timezone.utc),    # 3 min after
        appointment_time=None,                          # → R006 cannot_evaluate
        shipper_name=BILLED_PARTY,                      # exact match → R003 passes
        bol_terms=(
            "Shipper: Pacifica Apparel Inc.  Carrier: Empire Freight Lines.  "
            "Port of discharge: Long Beach, CA.  Container: MEDU9871234.  "
            "Terms: CY/CY.  Notify: Pacifica Apparel Inc., Los Angeles, CA."
        ),
        carrier_tariff_reference=TariffReference(
            tariff_number="EMFR-2024-DEM",
            effective_date=date(2026, 1, 1),
            published_free_time_hours=72.0,            # 72h published; invoice claims 48 → R004
        ),
        published_free_time_days=3.0,
        weather_events=[
            "ILWU 48-hour work stoppage, May 8–9, 2026.  Port of Long Beach "
            "terminal operations suspended by ILWU Local 13 job action; "
            "USMX–ILWU automation-clause dispute.  Terminal entry denied "
            "to all truckers 2026-05-08 00:00 through 2026-05-10 00:00 UTC."
        ],
        dispute_notice_date=None,
    )

    return DisputeCase(
        case_id="DEMO-CVF-2026-0001",
        invoice=invoice,
        evidence=evidence,
        created_at=datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc),
    )


def make_clean_violation_llm_responses() -> dict[str, dict]:
    """Return per-purpose LLM mock responses for the clean violation fixture.

    Keys are substrings matched against the LLM call's ``purpose`` argument.
    Values are dicts with keys: violated (bool|None), confidence (float),
    reasoning (str).

    The integration test mock helper matches the FIRST key that appears as
    a substring of the actual purpose string; use specific keys to avoid
    ambiguity.
    """
    return {
        "R002_basis_quality": {
            "violated": True,
            "confidence": 0.75,
            "reasoning": (
                "The basis_for_charge 'Demurrage charge per tariff EMFR-2024-DEM' "
                "is a generic tariff reference that does not explain the specific "
                "event triggering the charge, when it occurred, or why Pacifica "
                "Apparel Inc. is the responsible party.  §541.6(a)(4)'s substantive "
                "liability-basis requirement is not satisfied."
            ),
        },
        "R007_force_majeure": {
            "violated": True,
            "confidence": 0.92,
            "reasoning": (
                "Step 1: Named force majeure event — ILWU 48-hour work stoppage "
                "May 8–9, 2026.  "
                "Step 2: Temporal overlap confirmed — stoppage (May 8–10 UTC) fully "
                "overlaps the billed window (May 7–9).  "
                "Step 3: Terminal entry was denied to all truckers; container "
                "retrieval was physically impossible during the stoppage period.  "
                "Step 4: The §541.6(e)(2) certification is false — Empire Freight "
                "Lines cannot truthfully certify its performance did not cause the "
                "delay when a terminal-wide ILWU job action made retrieval impossible.  "
                "The charge cannot serve its incentive function per 85 FR 29638."
            ),
        },
    }
