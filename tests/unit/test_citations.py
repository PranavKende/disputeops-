"""Unit tests for agents.compliance_auditor.citations.

Test coverage targets (8 cases per spec):
  1. All 11 rule IDs (R001–R011) are present in CITATIONS.
  2. Every Citation.verbatim_text is non-empty and at least 20 characters.
  3. Every Citation.source_url is a valid HTTPS URL on a .gov domain
     (or regulation_part is marked as "derived").
  4. get_citation("R001") returns the correct citation.
  5. get_citation("R999") raises CitationNotFoundError.
  6. list_all_citations() returns exactly 11 citations sorted by rule_id.
  7. The CITATIONS dict cannot be mutated at runtime (TypeError).
  8. For every rule ID the rule_name matches the expected canonical name.

Additional sanity checks (beyond spec minimum) are grouped under the same
test IDs using sub-assertions so the spec count stays clean.
"""

from __future__ import annotations

import pytest

from agents.compliance_auditor.citations import (
    CITATIONS,
    Citation,
    get_citation,
    list_all_citations,
)
from agents.shared.exceptions import CitationNotFoundError

# ---------------------------------------------------------------------------
# Expected values — single source of truth for test assertions
# ---------------------------------------------------------------------------

EXPECTED_RULE_NAMES: dict[str, str] = {
    "R001": "Invoicing window",
    "R002": "Required minimum content",
    "R003": "Correct billing party",
    "R004": "Free time accuracy",
    "R005": "Gate timestamp consistency",
    "R006": "Appointment compliance",
    "R007": "Force majeure events",
    "R008": "Dispute notice period",
    "R009": "Charge calculation",
    "R010": "Free time consumption",
    "R011": "Per-day vs per-hour rule application",
    "R012": "Dispute mechanism disclosure",
}

ALL_RULE_IDS = sorted(EXPECTED_RULE_NAMES.keys())

# Rules whose regulation_part is "derived" — these are exempt from the .gov
# URL requirement because they have no direct CFR anchor.
DERIVED_RULE_IDS = {"R005", "R009"}


# ---------------------------------------------------------------------------
# Test 1 — all 11 rule IDs are present in CITATIONS
# ---------------------------------------------------------------------------


def test_all_rule_ids_present() -> None:
    """CITATIONS contains exactly the 11 expected rule IDs."""
    missing = [rid for rid in ALL_RULE_IDS if rid not in CITATIONS]
    assert not missing, f"Missing rule IDs in CITATIONS: {missing}"
    assert len(CITATIONS) == 12, (
        f"Expected 12 entries in CITATIONS, got {len(CITATIONS)}. "
        f"Extra IDs: {set(CITATIONS) - set(ALL_RULE_IDS)}"
    )


# ---------------------------------------------------------------------------
# Test 2 — every verbatim_text is non-empty and at least 20 characters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", ALL_RULE_IDS)
def test_verbatim_text_non_empty(rule_id: str) -> None:
    """Citation.verbatim_text for {rule_id} is non-empty and ≥ 20 chars."""
    citation = CITATIONS[rule_id]
    assert citation.verbatim_text, f"{rule_id}: verbatim_text is empty"
    assert len(citation.verbatim_text) >= 20, (
        f"{rule_id}: verbatim_text has only {len(citation.verbatim_text)} chars "
        f"(minimum 20 required)"
    )


@pytest.mark.parametrize("rule_id", ALL_RULE_IDS)
def test_supporting_authority_verbatim_text_non_empty(rule_id: str) -> None:
    """Every supporting authority on {rule_id} also has verbatim_text ≥ 20 chars."""
    citation = CITATIONS[rule_id]
    for i, auth in enumerate(citation.supporting_authorities):
        assert len(auth.verbatim_text) >= 20, (
            f"{rule_id} supporting_authorities[{i}]: verbatim_text too short "
            f"({len(auth.verbatim_text)} chars)"
        )


# ---------------------------------------------------------------------------
# Test 3 — every source_url is HTTPS on a .gov domain (or rule is derived)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", ALL_RULE_IDS)
def test_source_url_is_gov_https(rule_id: str) -> None:
    """Citation.source_url for {rule_id} is HTTPS on a .gov domain (or derived)."""
    citation = CITATIONS[rule_id]
    url_str = str(citation.source_url)

    if rule_id in DERIVED_RULE_IDS:
        # Derived rules still have a source_url — it points to the CFR section
        # they cross-reference.  Accept any .gov HTTPS URL for these too.
        assert url_str.startswith("https://"), (
            f"{rule_id} (derived): source_url should still be HTTPS, got {url_str!r}"
        )
        assert ".gov" in url_str, (
            f"{rule_id} (derived): source_url should be on a .gov domain, got {url_str!r}"
        )
    else:
        assert url_str.startswith("https://"), (
            f"{rule_id}: source_url is not HTTPS: {url_str!r}"
        )
        assert ".gov" in url_str, (
            f"{rule_id}: source_url is not on a .gov domain: {url_str!r}"
        )


@pytest.mark.parametrize("rule_id", ALL_RULE_IDS)
def test_supporting_authority_source_urls_are_gov_https(rule_id: str) -> None:
    """Every supporting authority source_url on {rule_id} is HTTPS on .gov."""
    citation = CITATIONS[rule_id]
    for i, auth in enumerate(citation.supporting_authorities):
        url_str = str(auth.source_url)
        assert url_str.startswith("https://"), (
            f"{rule_id} supporting_authorities[{i}]: source_url not HTTPS: {url_str!r}"
        )
        assert ".gov" in url_str, (
            f"{rule_id} supporting_authorities[{i}]: source_url not on .gov: {url_str!r}"
        )


# ---------------------------------------------------------------------------
# Test 4 — get_citation("R001") returns the correct citation
# ---------------------------------------------------------------------------


def test_get_citation_r001_returns_correct_entry() -> None:
    """get_citation('R001') returns the invoicing-window citation."""
    citation = get_citation("R001")
    assert isinstance(citation, Citation)
    assert citation.rule_id == "R001"
    assert citation.rule_name == "Invoicing window"
    # Verify the post-spec-correction subpart is §541.7(a), not §541.6
    assert citation.regulation_subpart == "541.7(a)", (
        f"R001 should cite §541.7(a) (post-2024 eCFR correction), "
        f"got {citation.regulation_subpart!r}"
    )
    assert "thirty (30) calendar days" in citation.verbatim_text, (
        "R001 verbatim_text should contain the 30-day invoicing window language"
    )
    assert "541.7" in citation.regulation_part or "541.7" in (citation.regulation_subpart or "")


# ---------------------------------------------------------------------------
# Test 5 — get_citation("R999") raises CitationNotFoundError
# ---------------------------------------------------------------------------


def test_get_citation_unknown_id_raises() -> None:
    """get_citation with an unknown rule ID raises CitationNotFoundError."""
    with pytest.raises(CitationNotFoundError) as exc_info:
        get_citation("R999")
    assert exc_info.value.rule_id == "R999"
    # CitationNotFoundError inherits from KeyError — verify the hierarchy
    assert isinstance(exc_info.value, KeyError)


def test_get_citation_malformed_id_raises() -> None:
    """get_citation with a malformed ID (not R###) also raises CitationNotFoundError."""
    with pytest.raises(CitationNotFoundError):
        get_citation("INVALID")


# ---------------------------------------------------------------------------
# Test 6 — list_all_citations() returns 11 citations sorted by rule_id
# ---------------------------------------------------------------------------


def test_list_all_citations_returns_11_sorted() -> None:
    """list_all_citations() returns exactly 11 entries in ascending rule_id order."""
    citations = list_all_citations()
    assert len(citations) == 12, f"Expected 12 citations, got {len(citations)}"
    ids = [c.rule_id for c in citations]
    assert ids == sorted(ids), f"Citations not sorted by rule_id: {ids}"
    assert ids == ALL_RULE_IDS, f"Rule ID mismatch: {ids} != {ALL_RULE_IDS}"


def test_list_all_citations_returns_citation_instances() -> None:
    """Every item returned by list_all_citations() is a Citation instance."""
    for item in list_all_citations():
        assert isinstance(item, Citation), f"Expected Citation, got {type(item)}"


# ---------------------------------------------------------------------------
# Test 7 — CITATIONS dict cannot be mutated at runtime
# ---------------------------------------------------------------------------


def test_citations_dict_is_immutable() -> None:
    """Attempting to add, update, or delete entries in CITATIONS raises TypeError."""
    with pytest.raises(TypeError):
        CITATIONS["R999"] = get_citation("R001")  # type: ignore[index]

    with pytest.raises(TypeError):
        CITATIONS["R001"] = get_citation("R001")  # type: ignore[index]

    with pytest.raises(TypeError):
        del CITATIONS["R001"]  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Test 8 — rule_name matches expected canonical name for every rule ID
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id,expected_name", EXPECTED_RULE_NAMES.items())
def test_rule_name_matches_expected(rule_id: str, expected_name: str) -> None:
    """Citation.rule_name for {rule_id} is {expected_name!r}."""
    citation = CITATIONS[rule_id]
    assert citation.rule_name == expected_name, (
        f"{rule_id}: expected rule_name={expected_name!r}, "
        f"got {citation.rule_name!r}"
    )


# ---------------------------------------------------------------------------
# Spot-check: post-correction subparts for the three corrected rules
# ---------------------------------------------------------------------------


def test_r008_cites_section_541_8_not_541_7() -> None:
    """R008 (dispute notice period) cites §541.8(a), corrected from spec's §541.7."""
    citation = get_citation("R008")
    assert "541.8" in citation.regulation_part or "541.8" in (citation.regulation_subpart or ""), (
        f"R008 should cite §541.8 (post-2024 correction), "
        f"got regulation_part={citation.regulation_part!r}, "
        f"regulation_subpart={citation.regulation_subpart!r}"
    )
    assert citation.regulation_subpart == "541.8(a)"


def test_r007_has_supporting_authority() -> None:
    """R007 (force majeure) has exactly one supporting authority referencing 85 FR 29638."""
    citation = get_citation("R007")
    assert len(citation.supporting_authorities) == 1, (
        f"R007 should have 1 supporting authority, got {len(citation.supporting_authorities)}"
    )
    auth = citation.supporting_authorities[0]
    assert auth.authority_type == "interpretive_rule"
    assert "85 FR 29638" in auth.citation
    assert "Fact Finding Investigation No. 28" in auth.citation


def test_r007_primary_cites_541_6e() -> None:
    """R007 primary citation is §541.6(e)(2), not a standalone force-majeure subpart."""
    citation = get_citation("R007")
    assert citation.regulation_subpart == "541.6(e)(2)", (
        f"R007 should cite §541.6(e)(2), got {citation.regulation_subpart!r}"
    )


# ---------------------------------------------------------------------------
# Effective-date sanity checks
# ---------------------------------------------------------------------------


def test_part541_effective_date_is_2024() -> None:
    """All Part 541 citations carry the May 28 2024 effective date."""
    from datetime import date

    expected = date(2024, 5, 28)
    for rule_id in ALL_RULE_IDS:
        citation = CITATIONS[rule_id]
        assert citation.effective_date == expected, (
            f"{rule_id}: effective_date={citation.effective_date!r}, expected {expected!r}"
        )


def test_r007_supporting_authority_effective_date_is_2020() -> None:
    """R007's supporting authority (85 FR 29638) carries the 2020-05-18 effective date."""
    # The supporting authority doesn't have its own effective_date field —
    # verify the citation string contains the date instead.
    auth = get_citation("R007").supporting_authorities[0]
    assert "2020" in auth.citation, (
        f"R007 supporting authority citation should reference 2020, got: {auth.citation!r}"
    )


# ---------------------------------------------------------------------------
# last_verified sanity check
# ---------------------------------------------------------------------------


def test_all_citations_last_verified_is_today() -> None:
    """All citations carry last_verified = 2026-05-27 (today's date)."""
    from datetime import date

    expected = date(2026, 5, 27)
    for rule_id in ALL_RULE_IDS:
        citation = CITATIONS[rule_id]
        assert citation.last_verified == expected, (
            f"{rule_id}: last_verified={citation.last_verified!r}, expected {expected!r}"
        )


# ---------------------------------------------------------------------------
# R012 spot-checks — Phase A clarification (R008/R012 split)
# ---------------------------------------------------------------------------


def test_r012_cites_section_541_6d() -> None:
    """R012 (dispute mechanism disclosure) cites §541.6(d) — the carrier-side rule."""
    citation = get_citation("R012")
    assert citation.regulation_subpart == "541.6(d)", (
        f"R012 should cite §541.6(d), got {citation.regulation_subpart!r}"
    )
    assert "541.6" in citation.regulation_part


def test_r012_verbatim_text_contains_contact_and_url_requirements() -> None:
    """R012 verbatim text covers both required §541.6(d) elements."""
    text = get_citation("R012").verbatim_text
    assert "contact information" in text.lower() or "telephone" in text.lower(), (
        "R012 verbatim text must reference the contact information requirement"
    )
    assert "url" in text.lower() or "digital" in text.lower(), (
        "R012 verbatim text must reference the URL/digital-means requirement"
    )


def test_r012_has_no_supporting_authorities() -> None:
    """R012 needs no supporting authorities — §541.6(d) is unambiguous."""
    assert get_citation("R012").supporting_authorities == []


def test_r008_and_r012_are_distinct_rules() -> None:
    """R008 and R012 audit different parties and must not share rule_name."""
    r008 = get_citation("R008")
    r012 = get_citation("R012")
    assert r008.rule_name != r012.rule_name
    # R008 audits the shipper's window; R012 audits the carrier's disclosure
    assert "disclosure" in r012.rule_name.lower()
    assert "window" in r008.rule_name.lower() or "period" in r008.rule_name.lower()


def test_list_all_citations_includes_r012_in_correct_position() -> None:
    """list_all_citations() places R012 after R011 (lexicographic sort)."""
    ids = [c.rule_id for c in list_all_citations()]
    assert ids[-1] == "R012", f"R012 should be last in sorted list, got: {ids}"
    assert ids[-2] == "R011"
