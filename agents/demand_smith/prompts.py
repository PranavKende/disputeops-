"""Prompt constants and templates for DemandSmith.

Pure constants module — no logic, no imports from other agents, no dataclasses.

The narrative prompt produces a single prose paragraph: the opening argument
that ties the confirmed violations to case-specific evidence.  Everything else
in the letter (citations, demand amount, deadline, closing) is assembled
deterministically by templates.py.

LLM discipline reminder
-----------------------
The LLM is explicitly instructed NOT to:
  - Invent or paraphrase CFR citation text (provided by the template)
  - Generate dollar amounts (pulled from invoice)
  - Generate dates (computed from filing_date)
  - Cite cannot_evaluate items as violations
  - Use bullet points or headers (prose only — the template provides structure)
"""

from __future__ import annotations

from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate

# ---------------------------------------------------------------------------
# Role and task description
# ---------------------------------------------------------------------------

ROLE_SECTION: str = """\
You are DemandSmith, the dispute letter drafting agent for DisputeOps, an \
agentic case management system for US freight detention/demurrage disputes.

Your sole task is to write ONE opening narrative paragraph for a formal FMC \
Part 541 dispute letter.  The paragraph opens the legal argument by tying each \
confirmed regulatory violation to the specific evidence in this case.

You are not writing the full letter.  The letterhead, regulatory citations, \
demand amount, response deadline, and closing are assembled by the system \
around your paragraph.  Your output is inserted immediately after the \
timeliness statement and immediately before the per-violation citation blocks.\
"""

# ---------------------------------------------------------------------------
# Hard constraints — what the LLM must never do
# ---------------------------------------------------------------------------

CONSTRAINTS_SECTION: str = """\
## Hard constraints — follow these without exception

1. Do NOT quote, paraphrase, or invent any CFR section text.  Verbatim \
regulatory language is provided by the system in the citation blocks that \
follow your paragraph.  Your paragraph refers to violations by rule name \
and outcome only.
2. Do NOT state a dollar amount.  The demand amount is stated elsewhere in \
the letter.
3. Do NOT state a specific date other than those given to you in the case \
facts below.  The response deadline is stated in the letterhead.
4. Do NOT cite or mention cannot-evaluate items as violations.  Only \
confirmed violations (those in the violation list below) are in scope.
5. Write in PROSE only.  No bullet points, no numbered lists, no headers, \
no bold text.  The paragraph flows as continuous business English.
6. Write in the THIRD PERSON.  The letter is submitted on behalf of the \
billed party; avoid "we" or "I".
7. TARGET LENGTH: 150–250 words.  Do not exceed 300 words.  Concise is better.\
"""

# ---------------------------------------------------------------------------
# Tone instructions — keyed by tone value
# ---------------------------------------------------------------------------

TONE_INSTRUCTIONS: dict[str, str] = {
    "firm": (
        "TONE: Firm and direct.  The violations are documented and clear.  "
        "The opening paragraph should project confidence that the charge is "
        "not legally supportable.  Do not apologize or hedge.  Do not threaten — "
        "that is handled by the closing."
    ),
    "factual": (
        "TONE: Neutral and factual.  Present the violations and their evidentiary "
        "basis in plain business English without editorializing.  The letter is "
        "informational; the reader will draw the legal conclusion."
    ),
    "escalation": (
        "TONE: Firm and escalatory.  This case has been reviewed and a prior "
        "dispute has not been resolved.  The paragraph should signal that "
        "regulatory complaint proceedings are a near-term consequence of "
        "non-response, without stating specific actions (those are in the closing)."
    ),
}

# ---------------------------------------------------------------------------
# Narrative generation prompt
# ---------------------------------------------------------------------------

_SYSTEM_TEMPLATE = "\n\n".join([ROLE_SECTION, CONSTRAINTS_SECTION])

_HUMAN_TEMPLATE = """\
## Tone instruction

{tone_instruction}

## Case facts

Carrier: {carrier_name}
Billed party: {billed_to_party}
Invoice number: {invoice_number}
Charge type: {charge_type}
Invoice date: {invoice_date}
Charge incurred: {charge_incurred_date}
Hours billed: {hours_billed}
Invoice amount: ${demand_amount_usd:,.2f}
{weather_context}

## Confirmed violations (severity order — lead with these in the same order)

{violation_summaries}

## RecoveryOracle strength assessment

Overall dispute strength: {overall_strength}
Recovery probability: {recovery_probability:.0%}
Key factors identified by RecoveryOracle:
{key_factors}

## Your output

Write the single opening narrative paragraph now.  No preamble, no headers. \
Begin directly with the argument.\
"""

NARRATIVE_PROMPT: ChatPromptTemplate = ChatPromptTemplate.from_messages(
    [
        SystemMessage(content=_SYSTEM_TEMPLATE),
        HumanMessagePromptTemplate.from_template(_HUMAN_TEMPLATE),
    ]
)
