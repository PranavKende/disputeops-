"""Demo fixture: Coastal Carrier Group / Heartland Imports LLC — borderline moderate case.

SCENARIO
--------
Coastal Carrier Group (NVOCC) billed Heartland Imports LLC $1,800.00 in
demurrage on container CMAU7723445 at the Port of Savannah, Georgia.
BOL CCGV20260512-334.  The container was picked up 72 hours late.

VIOLATIONS THAT FIRE (1, overall_strength = "moderate")
--------------------------------------------------------
R005 (conf=1.0, deterministic)
    gate_in_timestamp = 2026-05-12 07:42 UTC — 18 minutes before free_time_start
    (08:00 UTC).  The 15-minute tolerance allows up to 15 minutes early; 18
    minutes exceeds that threshold.  gate_out delta = 5 minutes (within
    tolerance).  One gate-timing violation fires.

RULES THAT PASS (8)
-------------------
R001  Invoice issued May 30, 2026 — 15 days after charge incurred May 15.
      Well within the 30-day §541.7(a) window.
R002  basis_for_charge is a 52-word substantive explanation naming the BOL,
      the specific late-pickup date, and the applicable tariff rate.
      Passes the deterministic quality check without LLM fallback.
R003  billed_to_party = "Heartland Imports LLC" = shipper_name — exact match.
R008  Dispute window status reporter — always violated=False.
R009  72h × $25.00/h = $1,800.00 — math correct within $0.01 tolerance.
R010  charge_incurred_date (May 15) == free_time_end.date() (May 15) —
      billing started the day free time expired, not before.
R011  charge_type = "demurrage"; hours_billed = 72.0 (72 % 24 == 0 — clean
      multiple of 24, so per-day billing is consistent).  No violation.
R012  basis_for_charge contains "disputes@coastalcarrier.com" and
      "https://coastalcarrier.com/dispute" — §541.6(d) satisfied.

RULES THAT CANNOT EVALUATE (3)
-------------------------------
R004  TariffReference CCGV-SAV-2025 has published_free_time_hours=None (not
      in structured tariff reference).  Falls through to LLM.  LLM mock
      returns violated=None, confidence=0.60; synthesize returns
      cannot_evaluate because violated is None.
R006  No appointment_time and no appointment_scheduled terminal records.
      Evidence gate fails — LLM never called.
R007  NOAA Coastal Flood Advisory present (evidence gate passes).  LLM mock
      returns violated=None, confidence=0.45; confidence < 0.60 threshold
      in _evaluate_R007_via_llm — returns cannot_evaluate directly.

EXPECTED VERDICT SHAPE
-----------------------
  overall_strength = "moderate"
  violations = [R005]
  cannot_evaluate = [R004, R006, R007]
  total_rules_evaluated = 9   (1 violation + 8 clean)
  summary contains: carrier name, container, "$1,800.00", "violation(s)"
  summary does NOT contain: "no FMC Part 541"

LLM MOCK REQUIREMENTS
----------------------
  "R004_free_time_bol_scan": violated=None, confidence=0.60
  "R007_force_majeure":       violated=None, confidence=0.45
  All other LLM purposes default to violated=False, confidence=0.85.

DATE ARITHMETIC
---------------
  free_time_start   = 2026-05-12 08:00 UTC
  free_time_end     = 2026-05-15 08:00 UTC   (exactly 72h; R011 clean multiple)
  charge_incurred   = 2026-05-15             (same date → R010 passes)
  invoice_date      = 2026-05-30             (15 days after charge → R001 passes)
  gate_in           = 2026-05-12 07:42 UTC   (18 min before → R005 fires)
  gate_out          = 2026-05-15 08:05 UTC   (5 min after → R005 passes)
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

CARRIER_NAME:      str   = "Coastal Carrier Group"
BILLED_PARTY:      str   = "Heartland Imports LLC"
CONTAINER_NUMBER:  str   = "CMAU7723445"
CHARGE_AMOUNT_USD: float = 1800.00
BOL_NUMBER:        str   = "CCGV20260512-334"

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_borderline_case() -> DisputeCase:
    """Return the Coastal Carrier Group / Heartland Imports borderline case.

    Produces 1 violation (R005 gate-timing), overall_strength = 'moderate',
    and 3 cannot_evaluate entries (R004 tariff-not-found, R006 no appointment,
    R007 low-confidence force majeure).  total_rules_evaluated = 9.
    """
    invoice = Invoice(
        invoice_number="CCGV-INV-2026-0334",
        carrier_name=CARRIER_NAME,
        bol_number=BOL_NUMBER,
        container_number=CONTAINER_NUMBER,
        charge_type="demurrage",
        charge_incurred_date=date(2026, 5, 15),
        invoice_date=date(2026, 5, 30),                 # 15 days → R001 passes
        free_time_start=datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc),
        free_time_end=datetime(2026, 5, 15, 8, 0, tzinfo=timezone.utc),   # 72h exactly
        hours_billed=72.0,                              # 72 % 24 == 0 → R011 passes
        hourly_rate_usd=25.0,
        total_amount_usd=CHARGE_AMOUNT_USD,             # 72 × 25 = 1800 → R009 passes
        billed_to_party=BILLED_PARTY,
        basis_for_charge=(
            "Container CMAU7723445 under BOL CCGV20260512-334 was not retrieved "
            "from Garden City Terminal, Savannah GA, within the 72-hour free period "
            "ending May 15, 2026.  Heartland Imports LLC, as the contracting shipper, "
            "is liable at tariff CCGV-SAV-2025 rate of $25.00/hour.  "
            "Dispute this charge at disputes@coastalcarrier.com or "
            "https://coastalcarrier.com/dispute within 30 days."
        ),
    )

    evidence = EvidencePackage(
        gate_in_timestamp=datetime(2026, 5, 12, 7, 42, tzinfo=timezone.utc),    # 18 min before → R005 violation
        gate_out_timestamp=datetime(2026, 5, 15, 8, 5, tzinfo=timezone.utc),     # 5 min after → passes
        appointment_time=None,                           # → R006 cannot_evaluate
        shipper_name=BILLED_PARTY,                       # exact match → R003 passes
        bol_terms=(
            "Shipper: Heartland Imports LLC, Atlanta GA.  "
            "Carrier: Coastal Carrier Group.  "
            "Port of discharge: Savannah, GA.  Container: CMAU7723445.  "
            "Terms: CY/CY.  Free time per tariff CCGV-SAV-2025.  "
            "Notify: Heartland Imports LLC, Atlanta GA."
        ),
        carrier_tariff_reference=TariffReference(
            tariff_number="CCGV-SAV-2025",
            effective_date=date(2026, 1, 1),
            published_free_time_hours=None,              # not in structured ref → R004 LLM path
        ),
        published_free_time_days=None,
        weather_events=[
            "NOAA Coastal Flood Advisory, Chatham County GA, May 12–13, 2026.  "
            "Moderate coastal flooding forecast; Garden City Terminal access road "
            "intermittently closed 2026-05-12 06:00 through 2026-05-13 18:00 UTC "
            "due to tidal surge.  Not a full terminal closure."
        ],
        dispute_notice_date=None,
    )

    return DisputeCase(
        case_id="DEMO-BDL-2026-0003",
        invoice=invoice,
        evidence=evidence,
        created_at=datetime(2026, 5, 30, 10, 0, tzinfo=timezone.utc),
    )


def make_borderline_llm_responses() -> dict[str, dict]:
    """Return per-purpose LLM mock responses for the borderline fixture.

    R004 falls through to LLM (tariff has no published_free_time_hours).
    R007 is called (weather event present) but confidence=0.45 < 0.60 threshold
    causes _evaluate_R007_via_llm to return cannot_evaluate directly.
    """
    return {
        "R004_free_time_bol_scan": {
            "violated": None,
            "confidence": 0.60,
            "reasoning": (
                "The BOL terms reference tariff CCGV-SAV-2025 for free time but do "
                "not state the specific number of hours.  Unable to determine whether "
                "the invoiced free time matches the published tariff without the full "
                "tariff document.  Insufficient evidence to evaluate."
            ),
        },
        "R007_force_majeure": {
            "violated": None,
            "confidence": 0.45,
            "reasoning": (
                "A NOAA Coastal Flood Advisory was in effect May 12–13, 2026 for "
                "Chatham County.  However, an 'advisory' is the lowest NWS severity "
                "level and does not necessarily close terminal access.  The record "
                "states access was 'intermittently' closed, not continuously.  "
                "Insufficient evidence to determine whether retrieval was impossible "
                "during the billed window.  Cannot evaluate."
            ),
        },
    }
