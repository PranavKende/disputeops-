"""LLM prompt constants for ComplianceAuditor rule evaluation.

Each constant is a ``ChatPromptTemplate`` with:
  - A system message carrying the rule's verbatim FMC citation (loaded from
    ``citations.get_citation()`` at import time — never hardcoded) and a
    role statement
  - A human message carrying the dynamic case data as ``{variables}``

All prompts instruct the LLM to set ``violated=None`` when evidence is
insufficient, and to cap confidence below 1.0 (deterministic certainty is
not appropriate for LLM-assisted evaluation).

Phase C prompts (mixed-rule LLM fallbacks): R002, R003, R004, R005.
Phase D prompts (LLM-primary rules):        R006, R007  — stubs below.
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from agents.compliance_auditor.citations import get_citation

# ---------------------------------------------------------------------------
# Shared instruction suffix appended to every system prompt
# ---------------------------------------------------------------------------
_STRUCTURED_OUTPUT_INSTRUCTIONS = """
Respond with a structured JSON object containing:
  - violated: true (violation found), false (compliant), or null (cannot
    determine due to insufficient evidence)
  - confidence: a float between 0.6 and 0.95. Never 1.0 — reserve full
    certainty for deterministic checks. Use lower values (0.6–0.7) when
    the evidence is thin or ambiguous.
  - reasoning: one to three sentences explaining your determination in
    plain English suitable for a freight dispute letter.

If evidence is insufficient to reach a conclusion, set violated to null
and explain what is missing in reasoning.
"""

# ---------------------------------------------------------------------------
# R002 — Basis-for-charge quality (LLM fallback)
# Triggered when basis_for_charge is present but suspiciously short or generic.
# ---------------------------------------------------------------------------
_R002_CITATION = get_citation("R002").verbatim_text

R002_BASIS_QUALITY_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        f"""You are a maritime compliance analyst evaluating FMC Part 541 violations.

Relevant regulation:
{_R002_CITATION}

Your task: determine whether the invoice's stated basis for charge constitutes
a *substantive* explanation of liability as required by §541.6(a). A substantive
basis must clearly explain (1) what event triggered the charge, (2) when it
occurred, and (3) why the billed party is responsible.

Generic phrases such as "detention charge", "per tariff", "standard rate",
or bare container numbers with no narrative do NOT satisfy §541.6(a)(4)'s
requirement to state "the basis for why the billed party is the proper party
of interest and thus liable for the charge."
{_STRUCTURED_OUTPUT_INSTRUCTIONS}""",
    ),
    (
        "human",
        "Invoice basis_for_charge: {basis_for_charge}\n\n"
        "Carrier: {carrier_name}\n"
        "Charge type: {charge_type}\n"
        "Billed to: {billed_to_party}",
    ),
])

# ---------------------------------------------------------------------------
# R003 — Billing party extraction from BOL (LLM fallback)
# Triggered when billed_to_party cannot be deterministically matched in bol_terms.
# ---------------------------------------------------------------------------
_R003_CITATION = get_citation("R003").verbatim_text

R003_BILLING_PARTY_FALLBACK_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        f"""You are a maritime compliance analyst evaluating FMC Part 541 violations.

Relevant regulation:
{_R003_CITATION}

Your task: read the Bill of Lading (BOL) terms provided and determine whether
the party invoiced ({'{billed_to_party}'}) is a legally correct billing target
under 46 CFR §541.4. The regulation allows billing only to (1) the contracting
party — the entity that arranged the ocean transportation — or (2) the consignee.

Extract the contracting party and consignee from the BOL text. If the invoiced
party matches neither, the invoice violates §541.4.
{_STRUCTURED_OUTPUT_INSTRUCTIONS}""",
    ),
    (
        "human",
        "BOL terms text:\n{bol_terms}\n\n"
        "Party invoiced on the detention/demurrage invoice: {billed_to_party}",
    ),
])

# ---------------------------------------------------------------------------
# R004 — Free time extraction from BOL text (LLM fallback)
# Triggered when TariffReference is absent but bol_terms may contain free-time terms.
# ---------------------------------------------------------------------------
_R004_CITATION = get_citation("R004").verbatim_text

R004_FREE_TIME_BOL_SCAN_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        f"""You are a maritime compliance analyst evaluating FMC Part 541 violations.

Relevant regulation:
{_R004_CITATION}

Your task: read the Bill of Lading text and extract any contractually agreed
free time (e.g. "4 free days", "72 hours free time", "2 days detention-free").
Then compare the extracted free time against the invoice's claimed free time
window. If the invoice claims less free time than the BOL grants, the carrier
understated free time and has overbilled — a §541.6(b) violation.

Express any free time you extract in hours for comparison.
{_STRUCTURED_OUTPUT_INSTRUCTIONS}""",
    ),
    (
        "human",
        "BOL terms text:\n{bol_terms}\n\n"
        "Invoice claims free time from {free_time_start} to {free_time_end} "
        "({claimed_free_time_hours:.1f} hours).",
    ),
])

# ---------------------------------------------------------------------------
# R005 — Gate timestamp extraction from free-text terminal records (LLM fallback)
# Triggered when gate timestamps are in TerminalRecord.notes rather than parsed fields.
# ---------------------------------------------------------------------------
_R005_CITATION = get_citation("R005").verbatim_text

R005_TIMESTAMP_PARSE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        f"""You are a maritime compliance analyst evaluating FMC Part 541 violations.

Relevant regulation:
{_R005_CITATION}

Your task: parse the terminal operating system (TOS) notes below to extract
gate-in and gate-out timestamps. Then determine whether these terminal-recorded
times are consistent with the invoice's claimed free-time window within a
15-minute tolerance.

A discrepancy greater than 15 minutes between terminal records and invoice
timestamps undermines the evidentiary basis for the specific dates charged
(§541.6(b) item 8) and may support a non-payment defence under §541.5.
{_STRUCTURED_OUTPUT_INSTRUCTIONS}""",
    ),
    (
        "human",
        "Terminal record notes:\n{terminal_record_notes}\n\n"
        "Invoice free time start: {invoice_free_time_start}\n"
        "Invoice free time end:   {invoice_free_time_end}",
    ),
])

# ---------------------------------------------------------------------------
# R006 — Appointment compliance  §541.6(e)(2)
# LLM-primary rule.  The rule function builds an evidence_summary string and
# passes it alongside the verbatim citation text.
# ---------------------------------------------------------------------------

R006_APPOINTMENT_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """You are a maritime compliance analyst evaluating FMC Part 541 detention and \
demurrage billing disputes.

Your task: determine whether the carrier's own performance caused or contributed \
to the billed delay, which would make the §541.6(e)(2) certification false and \
the charge invalid.

Relevant regulation (primary authority):
{citation}

Evaluation framework:
1. Identify the scheduled appointment time and the actual truck arrival time.
2. Determine whether the truck arrived on time for its appointment.
3. If the truck was late, assess whether the delay was caused by the receiver \
(dock congestion on the receiver's side, missing paperwork from the receiver, \
facility closure outside the carrier's control) or by the carrier (gate denial, \
scheduling error, premature gate closure, terminal-side congestion).
4. If the carrier's gate or scheduling process prevented the receiver from \
honouring an on-time appointment, the §541.6(e)(2) certification is false and \
the charge is invalid.

Confidence rubric:
- 0.95: Clear carrier-caused failure (e.g., gate denied a truck with valid appointment).
- 0.75: Probable carrier fault with some ambiguity in the record.
- 0.60: Genuinely close call — some evidence on both sides.
- Below 0.60: Set violated=null; the evidence is too thin to reach any conclusion.
"""
        + _STRUCTURED_OUTPUT_INSTRUCTIONS,
    ),
    (
        "human",
        "Appointment and gate evidence:\n{evidence_summary}\n\n"
        "Question: Did the carrier's own performance cause or contribute to the "
        "billed detention/demurrage delay, making the §541.6(e)(2) certification "
        "false?",
    ),
])

# ---------------------------------------------------------------------------
# R007 — Force majeure events  §541.6(e)(2) + 85 FR 29638
# LLM-primary rule.  The rule function passes both the primary citation
# (§541.6(e)(2)) and the supporting authority (85 FR 29638 incentive-function
# text) as separate named template variables.  They are presented to the LLM
# as distinct regulatory sources — the citation field on the returned RuleResult
# carries only the primary text.
# ---------------------------------------------------------------------------

R007_FORCE_MAJEURE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """You are a maritime compliance analyst evaluating FMC Part 541 detention and \
demurrage billing disputes involving potential force majeure events.

Primary regulation (§541.6(e)(2)):
{citation}

Supporting authority (85 FR 29638, FMC Interpretive Rule, May 18, 2020 — \
codified at 46 CFR Part 545; cross-referenced via §541.6(e)(1)):
{supporting_authority}

The supporting authority establishes the incentive-function test: D&D charges \
are unreasonable when circumstances outside the receiver's control prevent \
container retrieval or return, because under those circumstances the charges \
cannot serve their incentivizing purpose.

Follow these four reasoning steps in order:

Step 1 — Event identification: Identify any named force majeure event in the \
evidence (weather event, terminal closure, government action, or labor action). \
If none is present, the rule does not apply.

Step 2 — Temporal overlap: Determine whether the identified event occurred \
during the billed free-time window. A partial overlap is sufficient. If there \
is no overlap, the rule does not apply.

Step 3 — Incentive-function test: Assess whether the event prevented the \
receiver from retrieving or returning the container. Consider whether the \
event was outside the receiver's control and whether reasonable mitigation \
was possible.

Step 4 — Certification validity: If steps 1–3 all yield yes, the §541.6(e)(2) \
certification is false — the carrier cannot truthfully certify that its performance \
did not cause or contribute to the delay when a force majeure event made \
compliance impossible. The charge is therefore invalid.

Confidence rubric:
- 0.95: Named event with documented temporal overlap and clear impossibility \
  of retrieval (e.g., terminal closed by government order during entire billed period).
- 0.75: Strong overlap but some ambiguity about whether the receiver could \
  have retrieved the container through alternative means.
- 0.60: Partial overlap or ambiguous event characterisation.
- Below 0.60: Set violated=null; the evidence is too ambiguous or incomplete.
"""
        + _STRUCTURED_OUTPUT_INSTRUCTIONS,
    ),
    (
        "human",
        "Force majeure and billed-period evidence:\n{evidence_summary}\n\n"
        "Question: Did a force majeure event occur during the billed free-time "
        "window and prevent the receiver from retrieving or returning the container, "
        "making the §541.6(e)(2) certification false under the 85 FR 29638 "
        "incentive-function test?",
    ),
])
