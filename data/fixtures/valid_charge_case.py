"""Demo fixture: Pacific Liner Co. / Westcoast Distribution LLC — no-merit case.

SCENARIO
--------
Pacific Liner Co. (VOCC) billed Westcoast Distribution LLC $640.00 in
detention on container TCLU4470129 at the Port of Oakland, California.
BOL PACL20260315-774.  The container was returned 32 hours late.

VIOLATIONS THAT FIRE (0)
------------------------
None.  Every rule either passes or cannot evaluate.

RULES THAT PASS (11)
--------------------
R001  Invoice issued March 23, 2026 — 8 days after charge incurred March 15.
      Well within the 30-day §541.7(a) window.
R002  All required fields present.  basis_for_charge is 49-word substantive
      explanation naming the BOL, the specific late-return date, and the
      applicable tariff rate — passes the deterministic quality check without
      LLM fallback.
R003  billed_to_party = "Westcoast Distribution LLC" = shipper_name — exact
      match, deterministic pass.
R004  TariffReference PACL-2024-DET publishes 48.0 hours free time; invoice
      free_time_end − free_time_start = exactly 48.0 hours.  Delta = 0.0h.
R005  gate_in_timestamp is 4 minutes before free_time_start (delta = 4 min,
      within 15-min tolerance).  gate_out_timestamp is 3 minutes after
      free_time_end (delta = 3 min, within tolerance).
R006  appointment_time present.  LLM determines truck arrived on time for
      its scheduled appointment; carrier did not cause or contribute to the
      32-hour late return.  violated=False, confidence=0.88.
R008  Dispute window status reporter — always violated=False.
R009  32h × $20.00/h = $640.00 — math correct within $0.01 tolerance.
R010  charge_incurred_date (March 15) == free_time_end.date() (March 15) —
      billing started the day free time expired, not before.
R011  charge_type = "detention" — not applicable, violated=False.
R012  basis_for_charge contains "disputes@pacificliner.com" and
      "https://pacificliner.com/dispute" — §541.6(d) satisfied.

RULES THAT CANNOT EVALUATE (1)
-------------------------------
R007  No weather_events and no terminal_closure records.  Evidence gate fails;
      LLM never called.

EXPECTED VERDICT SHAPE
-----------------------
  overall_strength = "no_merit"
  violations = []
  cannot_evaluate = [R007]
  total_rules_evaluated = 11   (0 violations + 11 clean)
  summary contains: carrier name, container, "$640.00", "no FMC Part 541"
  summary does NOT contain: "violation(s) identified"

LLM MOCK REQUIREMENTS
----------------------
  "R006_appointment": violated=False, confidence=0.88
  All other LLM purposes default to violated=False, confidence=0.85.
  (R007 evidence gate fails → no LLM call for R007.)

DATE ARITHMETIC
---------------
  free_time_start   = 2026-03-13 08:00 UTC
  free_time_end     = 2026-03-15 08:00 UTC   (exactly 48h; matches tariff)
  charge_incurred   = 2026-03-15             (same date → R010 passes)
  invoice_date      = 2026-03-23             (8 days after charge → R001 passes)
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

CARRIER_NAME:      str   = "Pacific Liner Co."
BILLED_PARTY:      str   = "Westcoast Distribution LLC"
CONTAINER_NUMBER:  str   = "TCLU4470129"
CHARGE_AMOUNT_USD: float = 640.00
BOL_NUMBER:        str   = "PACL20260315-774"

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_valid_charge_case() -> DisputeCase:
    """Return the Pacific Liner Co. / Westcoast Distribution no-merit case.

    Produces 0 violations, overall_strength = 'no_merit', and 1
    cannot_evaluate entry (R007 — no force-majeure evidence in the package).
    Every deterministic rule passes; the single LLM rule (R006) returns
    violated=False via mock.
    """
    invoice = Invoice(
        invoice_number="PACL-INV-2026-0774",
        carrier_name=CARRIER_NAME,
        bol_number=BOL_NUMBER,
        container_number=CONTAINER_NUMBER,
        charge_type="detention",
        charge_incurred_date=date(2026, 3, 15),
        invoice_date=date(2026, 3, 23),                # 8 days → R001 passes
        free_time_start=datetime(2026, 3, 13, 8, 0, tzinfo=timezone.utc),
        free_time_end=datetime(2026, 3, 15, 8, 0, tzinfo=timezone.utc),   # 48h exactly
        hours_billed=32.0,                             # 32h late return
        hourly_rate_usd=20.0,
        total_amount_usd=CHARGE_AMOUNT_USD,            # 32 × 20 = 640 → R009 passes
        billed_to_party=BILLED_PARTY,
        basis_for_charge=(
            "Container TCLU4470129 under BOL PACL20260315-774 was returned to "
            "Oakland International Container Terminal on March 17, 2026 at 16:00 "
            "PST, 32 hours beyond the 48-hour free period ending March 15, 2026.  "
            "Westcoast Distribution LLC, as the contracting party, is liable at "
            "the tariff PACL-2024-DET rate of $20.00/hour.  "
            "Dispute this charge at disputes@pacificliner.com or "
            "https://pacificliner.com/dispute within 30 days."
        ),
    )

    evidence = EvidencePackage(
        gate_in_timestamp=datetime(2026, 3, 13, 7, 56, tzinfo=timezone.utc),   # 4 min before
        gate_out_timestamp=datetime(2026, 3, 15, 8, 3, tzinfo=timezone.utc),    # 3 min after
        appointment_time=datetime(2026, 3, 13, 8, 0, tzinfo=timezone.utc),
        shipper_name=BILLED_PARTY,                      # exact match → R003 passes
        bol_terms=(
            "Shipper: Westcoast Distribution LLC, Oakland CA.  "
            "Carrier: Pacific Liner Co.  "
            "Port of discharge: Oakland, CA.  Container: TCLU4470129.  "
            "Free time: 48 hours per tariff PACL-2024-DET.  "
            "Disputes: disputes@pacificliner.com"
        ),
        carrier_tariff_reference=TariffReference(
            tariff_number="PACL-2024-DET",
            effective_date=date(2026, 1, 1),
            published_free_time_hours=48.0,            # matches invoice → R004 passes
        ),
        published_free_time_days=2.0,
        weather_events=[],                             # none → R007 cannot_evaluate
        dispute_notice_date=None,
    )

    return DisputeCase(
        case_id="DEMO-VCC-2026-0002",
        invoice=invoice,
        evidence=evidence,
        created_at=datetime(2026, 3, 23, 9, 0, tzinfo=timezone.utc),
    )


def make_valid_charge_llm_responses() -> dict[str, dict]:
    """Return per-purpose LLM mock responses for the valid charge fixture.

    Only R006 calls the LLM (appointment evidence is present).
    R007's evidence gate fails (no weather events) — no LLM call is made.
    """
    return {
        "R006_appointment": {
            "violated": False,
            "confidence": 0.88,
            "reasoning": (
                "Truck arrived at 07:56 UTC for an 08:00 UTC appointment — within "
                "tolerance.  The 32-hour late return was due to Westcoast "
                "Distribution LLC's warehouse not being ready to accept the "
                "container; gate records confirm the terminal honored the "
                "appointment.  Carrier did not cause or contribute to the delay.  "
                "§541.6(e)(2) certification is valid."
            ),
        },
    }
