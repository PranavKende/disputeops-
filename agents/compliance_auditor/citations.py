"""FMC Part 541 regulatory citations for ComplianceAuditor rules R001–R012.

This module is the single source of truth for verbatim regulatory text used by:
  - ComplianceAuditor  — populates the ``citation`` field on every
                          ``Violation``, ``CannotEvaluate``, and ``RuleResult``
  - DemandSmith        — inserts quotable text into dispute letters
  - Test suite         — asserts citation accuracy and completeness

What this module IS:
    A static registry mapping each rule ID to its verbatim regulatory text,
    effective date, and authoritative source URL.  The text is sourced from
    the current Code of Federal Regulations and the Federal Register.

What this module is NOT:
    A legal interpretation layer.  This module records what the regulation
    says, not what it means in any specific factual context.  That
    determination belongs to the rule functions in ``rules.py`` and, where
    LLM reasoning is required, to the structured-output calls they invoke.

Regulatory baseline:
    46 CFR Part 541 as rewritten by the FMC's 2024 Demurrage and Detention
    Billing Requirements final rule, 89 FR 14330 (Feb. 26, 2024), effective
    May 28, 2024.  Verbatim text verified against the GovInfo XML publication
    at https://www.govinfo.gov/content/pkg/CFR-2024-title46-vol9/xml/
    CFR-2024-title46-vol9-part541.xml on 2026-05-27.

    The FMC 2020 Interpretive Rule (85 FR 29638, May 18, 2020, Docket No.
    19-05, Fact Finding Investigation No. 28) is cited as supporting authority
    for R007.  That rule amended 46 CFR Part 545 (Interpretations and
    Statements of Policy), not Part 541.  Part 545 is explicitly cross-
    referenced in the Part 541 certification at §541.6(e)(1), making the
    interpretive principles directly relevant to compliance determinations.

# ---------------------------------------------------------------------------
# Citation corrections from the original spec, based on current eCFR
# (Part 541 as rewritten by 89 FR 14330, effective May 28, 2024):
#
# R001  spec: §541.6          → actual: §541.7(a)
#        §541.6 is invoice content requirements; §541.7(a) is the 30-day
#        invoice issuance deadline.  Failure to meet it eliminates the
#        billed party's obligation to pay (§541.7(a) second sentence).
#
# R002  spec: §541.4          → actual: §541.6(a)–(c)
#        §541.4 governs who may be billed (billing party restrictions).
#        Required invoice content is §541.6(a) identifying info, §541.6(b)
#        timing info, and §541.6(c) rate info.  Consequence of omission is
#        §541.5.  verbatim_text drawn from §541.6 intro + §541.5;
#        regulation_subpart = None (spans (a)–(c)); summary cross-references.
#
# R003  spec: §541.4          → actual: §541.4           [CORRECT — no change]
#        Billing party restrictions; spec was right.
#
# R004  spec: §541.4(c)       → actual: §541.6(b) primary, §541.6(c) supporting
#        §541.4(c) does not exist in current Part 541.  Free-time dates and
#        tariff free-time days are §541.6(b) timing info; the tariff rate
#        reference is §541.6(c).  verbatim_text from §541.6(b);
#        regulation_subpart = "541.6(b)"; summary notes §541.6(c).
#
# R005  spec: derived         → actual: derived           [no change]
#        Industry-standard rule; no direct CFR anchor.  Cross-references
#        §541.6(b) item 8 (specific dates charged).
#
# R006  spec: §541.6(b)       → actual: §541.6(e)(2)
#        §541.6(b) is timing information.  Appointment/causation compliance
#        flows through the §541.6(e)(2) certification: "The billing party's
#        performance did not cause or contribute to the underlying invoiced
#        charges."
#
# R007  spec: §541.6(c)/composite → actual: §541.6(e)(2) + 85 FR 29638
#        No discrete force-majeure subpart exists in Part 541.  Force majeure
#        operates through §541.6(e)(2) causation certification, supported by
#        FMC Interpretive Rule 85 FR 29638 (codified at Part 545), which
#        names weather, terminal closures, and labor disruptions explicitly.
#        Note: §541.6(e)(1) explicitly cross-references 46 CFR 545.5,
#        making Part 545 compliance a certification requirement, not merely
#        persuasive authority.
#
# R008  spec: §541.7          → actual: §541.8(a)
#        §541.7 governs the billing party's 30-day invoice issuance window.
#        §541.8(a) gives the billed party ≥ 30 calendar days from invoice
#        issuance date to request mitigation, refund, or waiver.
#        PHASE A CLARIFICATION (R008/R012 split, 2026-05-27):
#        Original R008 conflated two distinct audits — shipper readiness and
#        carrier disclosure failure.  Split into:
#          R008 "Dispute window status" — audits the SHIPPER.  Checks whether
#               the §541.8(a) window is still open and whether dispute_notice_date
#               has been recorded.  Never returns violated=True; returns structured
#               status metadata in reasoning.  RecoveryOracle reads this field;
#               DemandSmith ignores it (it never appears in violations[]).
#          R012 "Dispute mechanism disclosure" — audits the CARRIER.  Checks
#               §541.6(d): did the invoice include contact information and a
#               URL/QR/process to dispute?  Violation if carrier omitted the
#               required disclosure.  Feeds DemandSmith.  Pure deterministic.
#
# R009  spec: derived         → actual: derived           [no change]
#        Arithmetic check implied by §541.6(c) (rate + total must be
#        consistent), but no explicit calculation-accuracy subpart exists.
#
# R010  spec: §541.6          → actual: §541.6(b)
#        Free-time start date, end date, and specific charge dates are all
#        required by §541.6(b) timing information items 4, 5, and 8.
#
# R011  spec: §541.6          → actual: §541.6(c) primary, §541.6(b) supporting
#        Per-day vs. per-hour billing validated against the tariff-rule
#        reference in §541.6(c) item 2 ("the daily rate is based").
#        §541.6(b) provides the charge dates for the per-day cross-check.
#
# R012  NEW (Phase A clarification, 2026-05-27) — §541.6(d)
#        Dispute mechanism disclosure; audits the carrier.  Not in original
#        spec.  Introduced to separate the carrier-side §541.6(d) disclosure
#        obligation from R008's shipper-side readiness check.
# ---------------------------------------------------------------------------
"""

from __future__ import annotations

from datetime import date
from types import MappingProxyType
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from agents.shared.exceptions import CitationNotFoundError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PART541_URL: str = (
    "https://www.govinfo.gov/content/pkg/CFR-2024-title46-vol9/xml/"
    "CFR-2024-title46-vol9-part541.xml"
)
_PART541_EFFECTIVE: date = date(2024, 5, 28)
_VERIFIED: date = date(2026, 5, 27)
_85FR29638_URL: str = (
    "https://www.govinfo.gov/content/pkg/FR-2020-05-18/html/2020-09370.htm"
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SupportingAuthority(BaseModel):
    """Non-CFR authority supporting interpretation of a primary CFR citation.

    Carries persuasive (or interpretive) authority such as FMC interpretive
    rules, policy statements, and advisory opinions.  DemandSmith uses this
    to write a second paragraph in dispute letters that clearly attributes
    each quoted passage to its correct authority tier — binding CFR text in
    paragraph one, interpretive guidance in paragraph two.

    Attributes:
        authority_type: Classification of the authority source.
        citation: Canonical bibliographic reference string, e.g.
            ``"FMC Interpretive Rule, 85 FR 29638 (May 18, 2020),
            Docket No. 19-05, Fact Finding Investigation No. 28"``.
        verbatim_text: Verbatim text from the authority document, quotable
            in demand letters.  Prefix ``"Derived:"`` if synthesised.
        source_url: URL to the published authority document.
        summary: One-sentence plain-English description for internal use.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority_type: Literal[
        "fmc_order",
        "interpretive_rule",
        "policy_statement",
        "advisory_opinion",
    ] = Field(..., description="Classification of the authority source")
    citation: str = Field(..., description="Canonical bibliographic reference")
    verbatim_text: str = Field(
        ...,
        min_length=20,
        description="Verbatim quotable text from the authority document",
    )
    source_url: HttpUrl = Field(..., description="URL to the published authority")
    summary: str = Field(..., description="One-sentence plain-English description")


class Citation(BaseModel):
    """Regulatory citation record for one FMC Part 541 compliance rule.

    Instances are frozen and validated at module load time via the
    ``CITATIONS`` registry.  Access them through ``get_citation()`` rather
    than the dict directly — that function raises ``CitationNotFoundError``
    on unknown IDs rather than a raw ``KeyError``.

    Attributes:
        rule_id: R001–R011 rule identifier.
        rule_name: Canonical short name matching the rule functions in
            ``rules.py``, e.g. ``"Invoicing window"``.
        regulation_part: Top-level regulatory part string, e.g.
            ``"46 CFR § 541.7"``.  Set to
            ``"Industry standard (derived)"`` for rules with no direct
            CFR anchor.
        regulation_subpart: Finer subpart reference, e.g. ``"541.7(a)"``.
            ``None`` when the rule spans multiple subparts or is derived.
        verbatim_text: Actual regulatory text, quotable in a demand letter.
            For derived or composite rules, begins with
            ``"Derived from §...: [text]. Source subparts: ..."`` to make
            the synthesis explicit; synthesised text is never presented as
            if it were verbatim CFR.
        summary: One-sentence plain-English summary for internal reasoning
            strings and logging — not for insertion into demand letters.
        effective_date: When the cited regulation (or subpart) took effect.
        source_url: Authoritative URL for the cited text.
        last_verified: Date the verbatim text was last confirmed current.
        supporting_authorities: Non-CFR authorities (FMC orders, interpretive
            rules, policy statements) that strengthen the primary citation.
            Used by DemandSmith to write a clearly attributed second
            paragraph; ``[]`` for rules with no supporting authority.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str = Field(..., pattern=r"^R\d{3}$")
    rule_name: str
    regulation_part: str
    regulation_subpart: str | None = None
    verbatim_text: str = Field(..., min_length=20)
    summary: str
    effective_date: date
    source_url: HttpUrl
    last_verified: date
    supporting_authorities: list[SupportingAuthority] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Registry — populated below, wrapped in MappingProxyType at module bottom
# ---------------------------------------------------------------------------

_CITATIONS_MUTABLE: dict[str, Citation] = {

    # ------------------------------------------------------------------
    # R001 — Invoicing window
    # Spec cited §541.6; corrected to §541.7(a) per current eCFR.
    # ------------------------------------------------------------------
    "R001": Citation(
        rule_id="R001",
        rule_name="Invoicing window",
        regulation_part="46 CFR § 541.7",
        regulation_subpart="541.7(a)",
        verbatim_text=(
            "A billing party must issue a demurrage or detention invoice within "
            "thirty (30) calendar days from the date on which the charge was last "
            "incurred. If the billing party does not issue a demurrage or detention "
            "invoice within thirty (30) calendar days from the date on which the "
            "charge was last incurred, then the billed party is not required to pay "
            "the charge."
        ),
        summary=(
            "Carriers must invoice within 30 calendar days of the last charge date; "
            "late invoices eliminate the billed party's payment obligation entirely."
        ),
        effective_date=_PART541_EFFECTIVE,
        source_url=_PART541_URL,  # type: ignore[arg-type]
        last_verified=_VERIFIED,
    ),

    # ------------------------------------------------------------------
    # R002 — Required minimum content
    # Spec cited §541.4; corrected to §541.6(a)–(c) + §541.5.
    # verbatim_text draws from §541.6 and §541.5; regulation_subpart=None
    # because the rule spans identifying (a), timing (b), and rate (c) info.
    # ------------------------------------------------------------------
    "R002": Citation(
        rule_id="R002",
        rule_name="Required minimum content",
        regulation_part="46 CFR § 541.6",
        regulation_subpart=None,
        verbatim_text=(
            # §541.6 opening + §541.5 consequence — composite of two sections;
            # subpart attribution: opening text from §541.6 preamble, consequence
            # from §541.5.  See summary for per-subpart attribution.
            "Composite of 46 CFR §§ 541.6 and 541.5. "
            "Per §541.6: A demurrage or detention invoice must contain, at a minimum: "
            "(a) Identifying information — the Bill of Lading number(s); the container "
            "number(s); for imports, the port(s) of discharge; the basis for why the "
            "billed party is the proper party of interest and thus liable for the charge. "
            "(b) Timing information — the invoice date; the invoice due date; the allowed "
            "free time in days; the start date of free time; the end date of free time; "
            "for imports, the container availability date; for exports, the earliest "
            "return date; the specific date(s) for which demurrage and/or detention were "
            "charged. "
            "(c) Rate information — the total amount due; the applicable detention or "
            "demurrage rule (e.g., the tariff name and rule number, terminal schedule, "
            "applicable service contract number and section, or applicable negotiated "
            "arrangement) on which the daily rate is based; the specific rate or rates "
            "per the applicable tariff rule or service contract. "
            "Per §541.5: Failure to include any of the required minimum information in "
            "this part in a demurrage or detention invoice eliminates any obligation of "
            "the billed party to pay the applicable charge."
        ),
        summary=(
            "Invoices must contain identifying info (§541.6(a)), timing info (§541.6(b)), "
            "and rate info (§541.6(c)); any omission eliminates the payment obligation "
            "under §541.5."
        ),
        effective_date=_PART541_EFFECTIVE,
        source_url=_PART541_URL,  # type: ignore[arg-type]
        last_verified=_VERIFIED,
    ),

    # ------------------------------------------------------------------
    # R003 — Correct billing party
    # Spec cited §541.4 — CORRECT, no change.
    # ------------------------------------------------------------------
    "R003": Citation(
        rule_id="R003",
        rule_name="Correct billing party",
        regulation_part="46 CFR § 541.4",
        regulation_subpart=None,
        verbatim_text=(
            "A properly issued invoice is a demurrage or detention invoice issued "
            "by a billing party to: (1) The person for whose account the billing "
            "party provided ocean transportation or storage of cargo and who "
            "contracted with the billing party for the ocean transportation or "
            "storage of cargo; or (2) The consignee. "
            "If a billing party issues a demurrage or detention invoice to the "
            "person identified in paragraph (a)(1) of this section, it cannot also "
            "issue a demurrage or detention invoice to the person identified in "
            "paragraph (a)(2). "
            "A billing party cannot issue an invoice to any other person."
        ),
        summary=(
            "A properly issued invoice may only be directed to the contracting party "
            "or the consignee — never both simultaneously, and never to any other "
            "party."
        ),
        effective_date=_PART541_EFFECTIVE,
        source_url=_PART541_URL,  # type: ignore[arg-type]
        last_verified=_VERIFIED,
    ),

    # ------------------------------------------------------------------
    # R004 — Free time accuracy
    # Spec cited §541.4(c) — subpart does not exist; corrected to §541.6(b)
    # (free-time timing items) primary, §541.6(c) (tariff rate ref) supporting.
    # ------------------------------------------------------------------
    "R004": Citation(
        rule_id="R004",
        rule_name="Free time accuracy",
        regulation_part="46 CFR § 541.6",
        regulation_subpart="541.6(b)",
        verbatim_text=(
            "Per §541.6(b) (Timing information): A demurrage or detention invoice "
            "must include, at a minimum: the allowed free time in days; the start "
            "date of free time; the end date of free time; for imports, the "
            "container availability date; the specific date(s) for which demurrage "
            "and/or detention were charged. "
            "Per §541.6(c) (Rate information): the applicable detention or demurrage "
            "rule (e.g., the tariff name and rule number, terminal schedule, applicable "
            "service contract number and section, or applicable negotiated arrangement) "
            "on which the daily rate is based; the specific rate or rates per the "
            "applicable tariff rule or service contract. "
            "Composite of §541.6(b) and §541.6(c). Source subparts: (b) free-time "
            "dates; (c) tariff-rule reference for published free time."
        ),
        summary=(
            "The invoice must state the contractually allowed free-time days (§541.6(b)) "
            "and cite the specific tariff rule on which the rate is based (§541.6(c)); "
            "a discrepancy between claimed free time and the carrier's filed tariff "
            "violates both subparts."
        ),
        effective_date=_PART541_EFFECTIVE,
        source_url=_PART541_URL,  # type: ignore[arg-type]
        last_verified=_VERIFIED,
    ),

    # ------------------------------------------------------------------
    # R005 — Gate timestamp consistency
    # Derived industry rule; no direct CFR anchor.
    # Cross-references §541.6(b) item 8 (specific dates charged).
    # ------------------------------------------------------------------
    "R005": Citation(
        rule_id="R005",
        rule_name="Gate timestamp consistency",
        regulation_part="Industry standard (derived)",
        regulation_subpart=None,
        verbatim_text=(
            "Derived from 46 CFR § 541.6(b): A demurrage or detention invoice must "
            "include 'the specific date(s) for which demurrage and/or detention were "
            "charged.' "
            "Source subparts: §541.6(b) item 8. "
            "Industry application: Driver-reported gate in/out timestamps must "
            "reconcile with terminal operating system (TOS) portal records within "
            "15 minutes to establish the billed period with the precision required "
            "by §541.6(b). A material discrepancy between driver logs and TOS records "
            "undermines the evidentiary basis for the specific dates charged and "
            "supports a §541.5 non-payment defence if the invoice cannot be "
            "independently verified."
        ),
        summary=(
            "No direct FMC subpart requires timestamp reconciliation; this rule is "
            "an industry-standard application of §541.6(b)'s requirement to state "
            "specific charge dates — a 15-minute tolerance is the accepted threshold "
            "for TOS/driver-log reconciliation in contested detention disputes."
        ),
        effective_date=_PART541_EFFECTIVE,
        source_url=_PART541_URL,  # type: ignore[arg-type]
        last_verified=_VERIFIED,
    ),

    # ------------------------------------------------------------------
    # R006 — Appointment compliance
    # Spec cited §541.6(b); corrected to §541.6(e)(2) causation certification.
    # ------------------------------------------------------------------
    "R006": Citation(
        rule_id="R006",
        rule_name="Appointment compliance",
        regulation_part="46 CFR § 541.6",
        regulation_subpart="541.6(e)(2)",
        verbatim_text=(
            "Per §541.6(e) (Certifications): A demurrage or detention invoice must "
            "certify that: "
            "(1) The charges are consistent with any of the Federal Maritime "
            "Commission's rules related to demurrage and detention, including, but "
            "not limited to, this part and 46 CFR 545.5; and "
            "(2) The billing party's performance did not cause or contribute to the "
            "underlying invoiced charges."
        ),
        summary=(
            "The carrier must certify that its own performance did not cause or "
            "contribute to the charged delay (§541.6(e)(2)); where an appointment "
            "was missed due to carrier-side gate denial, congestion, or scheduling "
            "failure, this certification is false and the charge is invalid."
        ),
        effective_date=_PART541_EFFECTIVE,
        source_url=_PART541_URL,  # type: ignore[arg-type]
        last_verified=_VERIFIED,
    ),

    # ------------------------------------------------------------------
    # R007 — Force majeure events
    # Spec implied §541.6(b)/(c); corrected to §541.6(e)(2) + 85 FR 29638.
    # No discrete force-majeure subpart exists in Part 541.  Causation
    # certification is the CFR hook; the 2020 interpretive rule (Part 545)
    # is supporting authority that explicitly names weather, terminal
    # closures, and labor actions.
    # Note: §541.6(e)(1) cross-references 46 CFR 545.5, making Part 545
    # compliance a certification requirement — not merely persuasive authority.
    # ------------------------------------------------------------------
    "R007": Citation(
        rule_id="R007",
        rule_name="Force majeure events",
        regulation_part="46 CFR § 541.6",
        regulation_subpart="541.6(e)(2)",
        verbatim_text=(
            "Per §541.6(e) (Certifications): A demurrage or detention invoice must "
            "certify that: "
            "(1) The charges are consistent with any of the Federal Maritime "
            "Commission's rules related to demurrage and detention, including, but "
            "not limited to, this part and 46 CFR 545.5; and "
            "(2) The billing party's performance did not cause or contribute to the "
            "underlying invoiced charges."
        ),
        summary=(
            "Weather, terminal closure, government action, and labour action negate "
            "the §541.6(e)(2) causation certification; a carrier cannot truthfully "
            "certify that its performance did not cause the delay when a force "
            "majeure event prevented container retrieval or return.  The §541.6(e)(1) "
            "cross-reference to 46 CFR 545.5 incorporates the FMC's interpretive "
            "principles — including the extenuating-circumstances standard — as a "
            "binding certification requirement, not merely persuasive guidance."
        ),
        effective_date=_PART541_EFFECTIVE,
        source_url=_PART541_URL,  # type: ignore[arg-type]
        last_verified=_VERIFIED,
        supporting_authorities=[
            SupportingAuthority(
                authority_type="interpretive_rule",
                citation=(
                    "FMC Interpretive Rule, 85 FR 29638 (May 18, 2020), "
                    "Docket No. 19-05, RIN 3072-AC76, Fact Finding Investigation No. 28"
                ),
                verbatim_text=(
                    "Importers, exporters, intermediaries, and truckers should not be "
                    "penalized by demurrage and detention practices when circumstances "
                    "are such that they cannot retrieve containers from, or return "
                    "containers to, marine terminals because under those circumstances "
                    "the charges cannot serve their incentive function. "
                    "Absent extenuating circumstances, practices and regulations that "
                    "provide for imposition of detention when it does not serve its "
                    "incentivizing purposes, such as when empty containers cannot be "
                    "returned, are likely to be found unreasonable. "
                    "[Separately:] Several [carriers] produced tariffs that specifically "
                    "state that free time is not automatically extended for events outside "
                    "the terminal's control, including labor strikes or weather, and at "
                    "least one said that in those circumstances free time would not be "
                    "adjusted."
                ),
                source_url=_85FR29638_URL,  # type: ignore[arg-type]
                summary=(
                    "The FMC's 2020 interpretive rule (codified at 46 CFR Part 545) "
                    "establishes that D&D charges are unreasonable when extenuating "
                    "circumstances outside the shipper's control — including labor "
                    "strikes, weather, and terminal closures — prevent container "
                    "retrieval or return, negating the charges' incentive function."
                ),
            ),
        ],
    ),

    # ------------------------------------------------------------------
    # R008 — Dispute notice period
    # Spec cited §541.7; corrected to §541.8(a).
    # §541.7 is the billing party's invoice-issuance deadline (R001).
    # §541.8(a) is the billed party's 30-day mitigation/dispute window.
    # ------------------------------------------------------------------
    "R008": Citation(
        rule_id="R008",
        rule_name="Dispute notice period",
        regulation_part="46 CFR § 541.8",
        regulation_subpart="541.8(a)",
        verbatim_text=(
            "The billing party must allow the billed party at least thirty (30) "
            "calendar days from the invoice issuance date to request mitigation, "
            "refund, or waiver of fees from the billing party. "
            "If a billing party receives a fee mitigation, refund, or waiver "
            "request, the billing party must attempt to resolve the request within "
            "thirty (30) calendar days of receiving such a request or at a later "
            "date as agreed upon by both parties."
        ),
        summary=(
            "The billed party has at least 30 calendar days from invoice issuance "
            "to file a dispute or request for mitigation/waiver (§541.8(a)); the "
            "billing party then has 30 days to attempt resolution (§541.8(b))."
        ),
        effective_date=_PART541_EFFECTIVE,
        source_url=_PART541_URL,  # type: ignore[arg-type]
        last_verified=_VERIFIED,
    ),

    # ------------------------------------------------------------------
    # R009 — Charge calculation
    # Derived from §541.6(c); no explicit calculation-accuracy subpart.
    # §541.6(c) requires total amount due and the specific rate(s) — an
    # invoice where total ≠ hours × rate is internally inconsistent with
    # its own §541.6(c) disclosures.
    # ------------------------------------------------------------------
    "R009": Citation(
        rule_id="R009",
        rule_name="Charge calculation",
        regulation_part="Industry standard (derived from 46 CFR § 541.6(c))",
        regulation_subpart=None,
        verbatim_text=(
            "Derived from 46 CFR § 541.6(c) (Rate information): A demurrage or "
            "detention invoice must include the total amount due and 'the specific "
            "rate or rates per the applicable tariff rule or service contract.' "
            "Source subpart: §541.6(c) items 1 and 3. "
            "Industry application: Where the invoice's total amount due does not "
            "equal the product of the hours or days billed multiplied by the stated "
            "rate, the invoice contains an internal arithmetic inconsistency.  Such "
            "an inconsistency undermines the invoice's compliance with §541.6(c)'s "
            "requirement that the rate and total be accurately stated, and may "
            "support a §541.5 non-payment defence on the grounds that the required "
            "rate information is effectively absent or inaccurate."
        ),
        summary=(
            "No CFR subpart explicitly mandates arithmetic accuracy, but §541.6(c) "
            "requires both the total amount and the specific rate — an invoice where "
            "total ≠ hours × rate violates the internal consistency of its own "
            "§541.6(c) disclosures."
        ),
        effective_date=_PART541_EFFECTIVE,
        source_url=_PART541_URL,  # type: ignore[arg-type]
        last_verified=_VERIFIED,
    ),

    # ------------------------------------------------------------------
    # R010 — Free time consumption
    # Spec cited §541.6; corrected to §541.6(b) (timing information).
    # Free-time start, end, and specific charge dates are all §541.6(b)
    # items 4, 5, and 8.
    # ------------------------------------------------------------------
    "R010": Citation(
        rule_id="R010",
        rule_name="Free time consumption",
        regulation_part="46 CFR § 541.6",
        regulation_subpart="541.6(b)",
        verbatim_text=(
            "Per §541.6(b) (Timing information): A demurrage or detention invoice "
            "must include, at a minimum: the allowed free time in days; the start "
            "date of free time; the end date of free time; for imports, the "
            "container availability date; the specific date(s) for which demurrage "
            "and/or detention were charged."
        ),
        summary=(
            "The invoice must state the free-time start and end dates and the "
            "specific dates billed (§541.6(b)); billing for dates that fall within "
            "the free-time window — i.e., before free time was fully exhausted — "
            "renders those charge dates inconsistent with the invoice's own §541.6(b) "
            "disclosures."
        ),
        effective_date=_PART541_EFFECTIVE,
        source_url=_PART541_URL,  # type: ignore[arg-type]
        last_verified=_VERIFIED,
    ),

    # ------------------------------------------------------------------
    # R011 — Per-day vs per-hour rule application
    # Spec cited §541.6; corrected to §541.6(c) primary, §541.6(b) supporting.
    # "Daily rate" is referenced in §541.6(c) item 2 ("the daily rate is based").
    # §541.6(b) charge dates provide the per-day cross-check.
    # Rule applies only when charge_type == 'demurrage'; for other charge types
    # evaluate_R011 returns a clean pass with explicit not-applicable reasoning.
    # ------------------------------------------------------------------
    "R011": Citation(
        rule_id="R011",
        rule_name="Per-day vs per-hour rule application",
        regulation_part="46 CFR § 541.6",
        regulation_subpart="541.6(c)",
        verbatim_text=(
            "Per §541.6(c) (Rate information): A demurrage or detention invoice "
            "must include the applicable detention or demurrage rule (e.g., the "
            "tariff name and rule number, terminal schedule, applicable service "
            "contract number and section, or applicable negotiated arrangement) "
            "on which the daily rate is based; and the specific rate or rates "
            "per the applicable tariff rule or service contract. "
            "Per §541.6(b) (Timing information): the invoice must also include "
            "the specific date(s) for which demurrage and/or detention were charged. "
            "Composite of §541.6(c) items 2–3 (rate basis and rate) and §541.6(b) "
            "item 8 (specific charge dates)."
        ),
        summary=(
            "Demurrage charges are conventionally applied per calendar day (not per "
            "hour); §541.6(c) requires the invoice to cite the tariff rule on which "
            "the 'daily rate is based,' so applying a per-hour rate to a per-day "
            "tariff produces a charge that is inconsistent with the carrier's own "
            "cited tariff rule."
        ),
        effective_date=_PART541_EFFECTIVE,
        source_url=_PART541_URL,  # type: ignore[arg-type]
        last_verified=_VERIFIED,
    ),

    # ------------------------------------------------------------------
    # R012 — Dispute mechanism disclosure  [NEW — Phase A clarification]
    # Introduced 2026-05-27 to separate the carrier-side §541.6(d)
    # disclosure obligation from R008's shipper-side readiness check.
    # This is a pure deterministic rule: §541.6(d) requires the invoice to
    # include (1) contact information and (2) a URL/QR/digital means to
    # the dispute process.  Absence of either element is a carrier violation.
    # No supporting authorities needed; §541.6(d) is unambiguous.
    #
    # Conservative implementation note (see also evaluate_R012 docstring):
    # The v1 deterministic check identifies clear absences (no available text
    # fields) but cannot detect *substantive* non-compliance (fields present
    # but containing irrelevant content).  False negatives possible; false
    # positives not.  A v2 LLM fallback would assess content quality.
    # ------------------------------------------------------------------
    "R012": Citation(
        rule_id="R012",
        rule_name="Dispute mechanism disclosure",
        regulation_part="46 CFR § 541.6",
        regulation_subpart="541.6(d)",
        verbatim_text=(
            "Per §541.6(d) (Dispute information): A demurrage or detention invoice "
            "must include, at a minimum: "
            "(1) The email, telephone number, or other appropriate contact information "
            "for questions or request for fee mitigation, refund, or waiver; "
            "(2) Digital means, such as a URL address, QR code, or digital watermark, "
            "that directs the billed party to a publicly accessible website; and "
            "(3) Defined timeframes that comply with the billing practices in this part, "
            "during which the billed party must request a fee mitigation, refund, or "
            "waiver."
        ),
        summary=(
            "The carrier must provide contact information and a digital link to the "
            "dispute process on every invoice (§541.6(d)); omission is a standalone "
            "carrier violation that DemandSmith cites directly — separate from the "
            "shipper's own dispute-window readiness tracked by R008."
        ),
        effective_date=_PART541_EFFECTIVE,
        source_url=_PART541_URL,  # type: ignore[arg-type]
        last_verified=_VERIFIED,
    ),
}

# Freeze the registry at module load time — mutation raises TypeError.
CITATIONS: Final[MappingProxyType[str, Citation]] = MappingProxyType(_CITATIONS_MUTABLE)


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------


def get_citation(rule_id: str) -> Citation:
    """Return the ``Citation`` registered for a given rule ID.

    Args:
        rule_id: A rule identifier matching the pattern ``R\\d{3}``,
            e.g. ``"R001"``.

    Returns:
        The ``Citation`` registered for that rule ID.

    Raises:
        CitationNotFoundError: If ``rule_id`` is not present in
            ``CITATIONS``.  Never returns ``None``.

    Example:
        >>> c = get_citation("R001")
        >>> c.rule_name
        'Invoicing window'
        >>> c.regulation_subpart
        '541.7(a)'
    """
    try:
        return CITATIONS[rule_id]
    except KeyError:
        raise CitationNotFoundError(rule_id)


def list_all_citations() -> list[Citation]:
    """Return all registered citations sorted by rule ID ascending.

    The sort is lexicographic on ``rule_id``, which matches numeric order
    for the R001–R011 range.  Intended for documentation generation,
    compliance reporting, and test assertions.

    Returns:
        A list of all 11 ``Citation`` objects in rule-ID order.

    Example:
        >>> citations = list_all_citations()
        >>> [c.rule_id for c in citations]
        ['R001', 'R002', 'R003', 'R004', 'R005', 'R006', 'R007', 'R008', 'R009', 'R010', 'R011']
    """
    return sorted(CITATIONS.values(), key=lambda c: c.rule_id)
