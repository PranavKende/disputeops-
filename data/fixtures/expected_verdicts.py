"""Expected verdict contracts for ComplianceAuditor integration tests.

Each ExpectedVerdict instance captures what the agent *must* produce for a
given fixture.  Integration tests use these constants rather than hard-coding
assertions inline, so a single change here updates every related assertion.

Design notes
------------
- ``expected_violations`` lists rule IDs that MUST appear in ComplianceVerdict.violations.
- ``expected_cannot_evaluate_min/max`` gives a tolerance band for cannot_evaluate
  entries (useful when an LLM path may add one extra CE depending on mock routing).
- ``expected_total_rules_evaluated`` is the exact count of violated + clean rules
  (cannot_evaluate entries are NOT counted per the spec).
- ``summary_must_contain`` items are checked with a case-sensitive substring match.
- ``summary_must_not_contain`` items must be absent (also case-sensitive).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExpectedVerdict(BaseModel):
    """Contract that a ComplianceVerdict must satisfy."""

    overall_strength: str
    """One of: 'strong', 'moderate', 'weak', 'no_merit'."""

    expected_violations: list[str] = Field(default_factory=list)
    """Rule IDs (e.g. 'R001') that must appear in verdict.violations."""

    expected_cannot_evaluate_min: int = 0
    """Minimum number of cannot_evaluate entries (inclusive lower bound)."""

    expected_cannot_evaluate_max: int = 12
    """Maximum number of cannot_evaluate entries (inclusive upper bound)."""

    expected_total_rules_evaluated: int
    """Exact value of ComplianceVerdict.total_rules_evaluated
    (= len(violations) + len(clean_results), cannot_evaluate NOT counted)."""

    summary_must_contain: list[str] = Field(default_factory=list)
    """Substrings that must appear in ComplianceVerdict.summary."""

    summary_must_not_contain: list[str] = Field(default_factory=list)
    """Substrings that must NOT appear in ComplianceVerdict.summary."""


# ---------------------------------------------------------------------------
# Clean violation case — Empire Freight Lines / Pacifica Apparel Inc.
# 6 violations: R001, R002, R004, R007, R011, R012; strength=strong
# ---------------------------------------------------------------------------

CLEAN_VIOLATION_EXPECTED = ExpectedVerdict(
    overall_strength="strong",
    expected_violations=["R001", "R002", "R004", "R007", "R011", "R012"],
    expected_cannot_evaluate_min=1,
    expected_cannot_evaluate_max=1,
    expected_total_rules_evaluated=11,
    summary_must_contain=[
        "Empire Freight Lines",
        "MEDU9871234",
        "$3,920.00",
        "violation(s)",
    ],
    summary_must_not_contain=[
        "no FMC Part 541",
    ],
)

# ---------------------------------------------------------------------------
# Valid charge case — Pacific Liner Co. / Westcoast Distribution LLC
# 0 violations; strength=no_merit; 1 cannot_evaluate (R007)
# ---------------------------------------------------------------------------

VALID_CHARGE_EXPECTED = ExpectedVerdict(
    overall_strength="no_merit",
    expected_violations=[],
    expected_cannot_evaluate_min=1,
    expected_cannot_evaluate_max=1,
    expected_total_rules_evaluated=11,
    summary_must_contain=[
        "Pacific Liner Co.",
        "TCLU4470129",
        "$640.00",
        "no FMC Part 541",
    ],
    summary_must_not_contain=[
        "violation(s) identified",
    ],
)

# ---------------------------------------------------------------------------
# Borderline case — Coastal Carrier Group / Heartland Imports LLC
# 1 violation: R005; strength=moderate; 3 cannot_evaluate (R004, R006, R007)
# ---------------------------------------------------------------------------

BORDERLINE_EXPECTED = ExpectedVerdict(
    overall_strength="moderate",
    expected_violations=["R005"],
    expected_cannot_evaluate_min=3,
    expected_cannot_evaluate_max=3,
    expected_total_rules_evaluated=9,
    summary_must_contain=[
        "Coastal Carrier Group",
        "CMAU7723445",
        "$1,800.00",
        "violation(s)",
    ],
    summary_must_not_contain=[
        "no FMC Part 541",
    ],
)
