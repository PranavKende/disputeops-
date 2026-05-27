"""Demo-quality DisputeCase fixtures for ComplianceAuditor integration tests.

Three fixtures cover the full dispute-strength spectrum:

  clean_violation_case  — Empire Freight Lines / Pacifica Apparel Inc.
                          Strong case (5 violations including force majeure).
  valid_charge_case     — Pacific Liner Co. / Westcoast Distribution LLC
                          No-merit case (0 violations, all rules pass).
  borderline_case       — Coastal Carrier Group / Heartland Imports LLC
                          Moderate case (1 gate-timing violation, 3 cannot-evaluate).

Each fixture module exports:
  - Named constants (CARRIER_NAME, CONTAINER_NUMBER, CHARGE_AMOUNT_USD, ...)
  - make_<fixture>_case() → DisputeCase
  - make_<fixture>_llm_responses() → dict[str, dict]
    Keys are substrings of the LLM call's ``purpose`` argument;
    values are {violated, confidence, reasoning} dicts for mock configuration.
"""
