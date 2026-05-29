"""Input schemas for a DisputeOps detention/demurrage dispute case.

These models represent the data ingested from DisputeOps Stage 2 (evidence
gathering) before being handed to ComplianceAuditor in Stage 3.

# TODO (v2): Wrap evidence primitives in EvidenceField[T] for per-field provenance
#            (source, confidence, retrieved_at). Deferred from v1 to keep schemas lean.
#            Will be needed once Stage 2 has multiple parallel evidence sources that
#            can disagree. See data/schemas/README.md for the full rationale.
#
# TODO (v2): Add Invoice.dispute_contact and Invoice.dispute_url fields for cleaner
#            R012 evaluation. R012 currently uses basis_for_charge and bol_terms as
#            text proxies for the §541.6(d) dispute-mechanism disclosure — deferred
#            from v1 to keep the Invoice schema lean.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class TariffReference(BaseModel):
    """A structured reference to a carrier's FMC-filed tariff.

    Using a sub-model (rather than a raw string) allows R004 to compare
    published free time against invoice claims without regex guesswork.

    Attributes:
        tariff_number: The FMC tariff filing number, e.g. "SEAU-020".
        effective_date: Date this tariff schedule took effect.
        fmc_filing_url: Direct URL to the FMC eTariff filing, if available.
        published_free_time_hours: Convenience field — free time in hours as
            published in this tariff.  Should equal
            ``EvidencePackage.published_free_time_days × 24``.  An
            ``EvidencePackage`` model_validator logs a warning when the two
            fields disagree by more than 0.1 hours, since the disagreement
            itself is an evidence-quality signal (InvoiceLens may have
            extracted one but not the other, or they may genuinely conflict).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tariff_number: str = Field(..., description="FMC tariff filing number, e.g. 'SEAU-020'")
    effective_date: date | None = Field(
        default=None, description="Date this tariff schedule took effect"
    )
    fmc_filing_url: HttpUrl | None = Field(
        default=None, description="Direct URL to the FMC eTariff filing"
    )
    published_free_time_hours: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Free time hours from the tariff filing. "
            "Should equal EvidencePackage.published_free_time_days × 24. "
            "PaperTrail (Stage 2) is responsible for populating both fields "
            "consistently; a mismatch is flagged by EvidencePackage validator."
        ),
    )


class TerminalRecord(BaseModel):
    """A single record pulled from the terminal operating system (TOS).

    Attributes:
        record_id: Unique identifier from the TOS export.
        event_type: The kind of event captured (e.g. "gate_in", "gate_out",
            "chassis_return", "container_available", "vessel_discharge").
        event_timestamp: UTC timestamp of the event.
        container_number: Container to which this event belongs.
        source: Name of the terminal / TOS that produced this record
            (e.g. "TRAPAC_LA", "GCT_BAYONNE").
        notes: Free-text field from the TOS, if present.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: str = Field(..., description="Unique ID from TOS export")
    event_type: Literal[
        "gate_in",
        "gate_out",
        "chassis_return",
        "container_available",
        "vessel_discharge",
        "appointment_scheduled",
        "appointment_cancelled",
        "terminal_closure",
        "other",
    ] = Field(..., description="Type of terminal event")
    event_timestamp: datetime = Field(..., description="UTC timestamp of the event")
    container_number: str = Field(..., description="ISO container number, e.g. MSCU1234567")
    source: str = Field(..., description="Terminal / TOS name that produced this record")
    notes: str | None = Field(default=None, description="Free-text TOS notes")


class Invoice(BaseModel):
    """The carrier's detention or demurrage invoice.

    This mirrors the structured fields extracted from the raw invoice PDF
    during Stage 2 evidence gathering.

    Attributes:
        invoice_number: Carrier-assigned invoice identifier.
        carrier_name: Full legal name of the billing carrier or terminal.
        bol_number: Bill of Lading number. May be absent if not yet parsed.
        container_number: ISO container number. May be absent for detention-
            only invoices that reference only the BOL.
        charge_type: Classification of the charge as one of the standard
            freight charge categories.
        charge_incurred_date: The calendar date the charge started accruing.
            For detention this is the day free time expired; for demurrage
            it is the day after last free day.
        invoice_date: The date printed on the invoice (used for R001 check).
        free_time_start: When free time began (arrival / vessel discharge).
        free_time_end: When free time expired and billing started.
        hours_billed: Always stored in hours, even for demurrage invoices that
            the carrier originally expressed in days.  PaperTrail (Stage 2,
            ``agents/paper_trail/``) normalizes daily-billed invoices to hours
            at extraction time (``days × 24 → hours``).  R011 converts back
            via ``hours_billed / 24`` when validating per-day demurrage
            charges.  See ``billed_unit_inferred`` for a heuristic that
            detects whether the carrier billed hourly or daily.
        hourly_rate_usd: The per-hour (or per-day) rate stated on the invoice.
        total_amount_usd: The total dollar amount on the invoice.
        billed_to_party: Name or SCAC of the entity the invoice was addressed
            to (used for R003 billing-party check).
        basis_for_charge: Carrier's stated reason / narrative for the charge,
            as printed on the invoice.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    invoice_number: str = Field(..., description="Carrier-assigned invoice ID")
    carrier_name: str = Field(..., min_length=1, description="Full legal name of billing carrier")
    bol_number: str | None = Field(default=None, description="Bill of Lading number")
    container_number: str | None = Field(
        default=None,
        description="ISO container number, e.g. MSCU1234567",
    )
    charge_type: Literal["detention", "demurrage", "tonu", "layover", "lumper", "other"] = Field(
        ..., description="Classification of the freight charge"
    )
    charge_incurred_date: date = Field(
        ..., description="Calendar date the charge began accruing"
    )
    invoice_date: date = Field(..., description="Date printed on the invoice")
    free_time_start: datetime | None = Field(
        default=None,
        description="UTC datetime when free time began",
    )
    free_time_end: datetime | None = Field(
        default=None,
        description="UTC datetime when free time expired and billing started",
    )
    hours_billed: Annotated[float, Field(ge=0)] = Field(
        ..., description="Hours billed (detention) or days × 24 (demurrage)"
    )
    hourly_rate_usd: Annotated[float, Field(ge=0)] = Field(
        ..., description="Per-hour or per-day rate stated on the invoice (USD)"
    )
    total_amount_usd: Annotated[float, Field(ge=0)] = Field(
        ..., description="Total invoice amount (USD)"
    )
    billed_to_party: str = Field(
        ..., description="Name or SCAC of the entity invoiced"
    )
    basis_for_charge: str | None = Field(
        default=None, description="Carrier's stated reason for the charge"
    )

    @property
    def billed_unit_inferred(self) -> Literal["hourly", "daily"]:
        """Heuristic: clean multiples of 24 suggest the carrier billed daily and
        normalized to hours.  Used by R011 reasoning strings only — not for
        any decision logic.

        Returns:
            ``"daily"`` if ``hours_billed`` is a non-zero clean multiple of 24,
            otherwise ``"hourly"``.

        Examples:
            >>> Invoice(..., hours_billed=72.0).billed_unit_inferred
            'daily'
            >>> Invoice(..., hours_billed=8.5).billed_unit_inferred
            'hourly'
        """
        return "daily" if self.hours_billed % 24 == 0 and self.hours_billed >= 24 else "hourly"

    @model_validator(mode="after")
    def free_time_window_is_consistent(self) -> "Invoice":
        """Ensure free_time_end is after free_time_start when both are present."""
        if self.free_time_start and self.free_time_end:
            if self.free_time_end <= self.free_time_start:
                raise ValueError(
                    "free_time_end must be strictly after free_time_start; "
                    f"got start={self.free_time_start!r}, end={self.free_time_end!r}"
                )
        return self


class EvidencePackage(BaseModel):
    """All evidence collected by Stage 2 (evidence gathering agent).

    Fields are all optional because evidence may be partial — ComplianceAuditor
    must handle missing evidence gracefully via ``cannot_evaluate`` results
    rather than raising exceptions.

    Attributes:
        gate_in_timestamp: UTC timestamp the truck entered the terminal gate
            (from driver log or terminal portal).
        gate_out_timestamp: UTC timestamp the truck exited the terminal gate.
        appointment_time: Scheduled appointment time (UTC), if one was made.
        pod_signed_at: UTC timestamp the proof of delivery was signed.
        terminal_records: Structured records pulled from the terminal TOS.
        weather_events: Plain-text descriptions of weather or force-majeure
            events that occurred during the detention period, e.g.
            "Hurricane Ian landfall 2022-09-28, USPS issued advisory."
        bol_terms: Raw text of the Bill of Lading terms and conditions,
            used to determine the legally responsible party (R003).
        carrier_tariff_reference: Structured reference to the carrier's
            FMC-filed tariff, used to verify published free time (R004).
            Promoted from a raw string to a ``TariffReference`` sub-model so
            R004 can compare structured data without regex.
        published_free_time_days: Free time days published in the carrier's
            filed tariff, if extracted by PaperTrail.  Used for R004 and R010.
            A model_validator warns when this disagrees with
            ``carrier_tariff_reference.published_free_time_hours / 24``
            by more than 0.1 hours — the disagreement is itself an
            evidence-quality signal rather than a hard error.
        dispute_notice_date: Date the shipper submitted a formal dispute
            notice, if any (used for R008 window check).
        shipper_name: Full legal name of the shipper per the BOL, used to
            cross-check R003 (correct billing party).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    gate_in_timestamp: datetime | None = Field(
        default=None, description="UTC gate-in from driver log or terminal portal"
    )
    gate_out_timestamp: datetime | None = Field(
        default=None, description="UTC gate-out from driver log or terminal portal"
    )
    appointment_time: datetime | None = Field(
        default=None, description="Scheduled appointment time (UTC)"
    )
    pod_signed_at: datetime | None = Field(
        default=None, description="UTC timestamp proof-of-delivery was signed"
    )
    terminal_records: list[TerminalRecord] = Field(
        default_factory=list,
        description="Structured records from the terminal TOS",
    )
    weather_events: list[str] = Field(
        default_factory=list,
        description="Plain-text force-majeure event descriptions",
    )
    bol_terms: str | None = Field(
        default=None, description="Raw text of BOL terms and conditions"
    )
    carrier_tariff_reference: TariffReference | None = Field(
        default=None, description="Structured reference to the carrier's FMC-filed tariff"
    )
    published_free_time_days: Annotated[float, Field(ge=0)] | None = Field(
        default=None,
        description=(
            "Free time days from carrier's filed tariff (extracted by PaperTrail). "
            "Used for R004 and R010.  Should be consistent with "
            "carrier_tariff_reference.published_free_time_hours / 24."
        ),
    )
    dispute_notice_date: date | None = Field(
        default=None, description="Date shipper filed a formal dispute notice"
    )
    shipper_name: str | None = Field(
        default=None, description="Full legal name of shipper per the BOL"
    )

    @model_validator(mode="after")
    def warn_if_free_time_fields_disagree(self) -> "EvidencePackage":
        """Log a warning when tariff free-time fields disagree by > 0.1 hours.

        This is a soft check — disagreement is an evidence-quality signal
        (InvoiceLens may have extracted one field but not the other, or they
        may genuinely conflict between the tariff filing and the BOL).  It is
        never a hard validation error.
        """
        if (
            self.carrier_tariff_reference is not None
            and self.carrier_tariff_reference.published_free_time_hours is not None
            and self.published_free_time_days is not None
        ):
            tariff_hours = self.carrier_tariff_reference.published_free_time_hours
            days_as_hours = self.published_free_time_days * 24
            if abs(tariff_hours - days_as_hours) > 0.1:
                logger.warning(
                    "EvidencePackage free-time inconsistency: "
                    "carrier_tariff_reference.published_free_time_hours=%.2f "
                    "vs published_free_time_days × 24=%.2f (delta=%.2f). "
                    "Check PaperTrail extraction for case evidence.",
                    tariff_hours,
                    days_as_hours,
                    abs(tariff_hours - days_as_hours),
                )
        return self


# ---------------------------------------------------------------------------
# Top-level case model
# ---------------------------------------------------------------------------


class DisputeCase(BaseModel):
    """A single detention/demurrage dispute case passed to ComplianceAuditor.

    This is the root input model for the agent.  It is produced by the Stage 2
    evidence-gathering agent and is immutable once created.

    Attributes:
        case_id: Globally unique case identifier (UUID or human-readable slug).
        invoice: The structured invoice to be audited.
        evidence: All evidence collected to support or refute the charge.
        created_at: UTC timestamp when the case was created in DisputeOps.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(..., min_length=1, description="Globally unique case ID")
    invoice: Invoice = Field(..., description="The carrier invoice under audit")
    evidence: EvidencePackage = Field(
        ..., description="Evidence package from Stage 2 gathering"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the case was created",
    )
