# DemandSmith — coding-agent evidence

[![Tests](https://img.shields.io/badge/tests-80_passing-brightgreen)]()
[![Framework](https://img.shields.io/badge/framework-LangChain-purple)]()
[![Stage](https://img.shields.io/badge/stage-3_(Analysis)-blue)]()

This document is the evidence record for the use of **Claude Code (Anthropic)** in building the DemandSmith agent — the FMC dispute letter drafter in Stage 3 of the DisputeOps case lifecycle.

Submitted as evidence under the AgentHack 2026 +2 coding-agent bonus criteria.

## What was built

DemandSmith is a LangChain agent that consumes a `ComplianceVerdict` (from ComplianceAuditor), a `RecoveryScore` (from RecoveryOracle), and the underlying `DisputeCase` to produce a formal FMC Part 541 dispute letter — ready to send to the carrier.

Why LangChain (not LangGraph, not Pydantic AI): letter generation is template-with-LLM-narrative, not multi-step orchestration and not type-safe single-call reasoning. LangChain's `ChatPromptTemplate` + plain LLM call is the natural fit. This is the third distinct framework across the three Stage 3 coded agents (LangGraph for ComplianceAuditor / Pydantic AI for RecoveryOracle / LangChain for DemandSmith), reinforcing the "deliberate, deep usage of multiple external frameworks within a governed UiPath orchestration layer" pattern the hackathon rules reward.

| Component | Count |
|---|---|
| LangChain agent | 1 |
| Pydantic v2 schemas | 1 new (`DemandLetter`) |
| Letter sections (templated) | 7 (letterhead, timeliness paragraph, narrative, citation blocks, demand statement, closing, signature block) |
| Tone variants | 3 (firm, factual, escalation) |
| Total tests | 80 |
| &nbsp;&nbsp;— Unit | 55 |
| &nbsp;&nbsp;— Integration | 25 |
| Lines of code (excluding tests) | ~880 |
| Test code | ~1,150 |

## The template-vs-LLM split (the key design discipline)

DemandSmith is the third agent in the project where template-vs-LLM discipline shaped the architecture (after ComplianceAuditor's "rules are citation consumers, not composers" and RecoveryOracle's "arithmetic isolated from LLM reasoning"). For DemandSmith, the split is:

**Templated (deterministic, never LLM)**:
- Letterhead — date, carrier name, invoice number, BOL, container, demand amount, response deadline
- §541.8(a)/(b) dual-framing paragraph — timeliness assertion + carrier response obligation, all dates computed
- Per-violation citation blocks — verbatim regulatory text from `Citation.verbatim_text`
- Supporting authority blocks (R007) — verbatim text from `Citation.supporting_authorities[0].verbatim_text`
- Demand amount and FMC complaint escalation language
- Signature block

**LLM-generated (single chain call)**:
- The narrative paragraph that opens the legal argument, ties violations to specific evidence, and walks violations in severity order

The LLM never invents a citation, a dollar amount, a date, or a regulatory subpart. The template never invents a sentence of argument. This split is what makes the letter both legally defensible (cited text is verbatim) and persuasive (narrative is contextual prose).

## Build phases

| Phase | Scope | Tests added | Outcome |
|---|---|---|---|
| Phase 1 — Design | DemandLetter schema, template structure, prompt structure, tone-injection design, three design decisions surfaced | 0 | Locked the template-vs-LLM split, one-narrative-not-N decision, severity-ordering of violations |
| Phase 2 — Implementation | `letter.py`, `templates.py`, `prompts.py`, `demand_smith.py`, `__init__.py` | 0 | Generated the first end-to-end letter for clean_violation_case |
| Phase 3 — Test suite | Unit + integration tests, real chain execution via FakeListChatModel | 80 | Confirmed the LLM chain actually executes; date-determinism via explicit filing_date throughout |

## Three architectural decisions during Phase 1

1. **One narrative paragraph, not per-violation paragraphs.** N LLM calls would produce N round-trips, incoherent tone across paragraphs, and risk evidence repetition. One call over all violations produces a coherent opening argument; the deterministic citation blocks provide per-rule legal detail. This is how real demand letters read.

2. **Violations sorted severity-first in the narrative.** R001 (47-day window miss → carrier legally cannot collect) is dispositive on its own and leads. R012 (missing dispute URL) is minor procedural and trails. The narrative prompt receives pre-sorted violations; the deterministic citation blocks follow the same order.

3. **Response deadline cites §541.8(b), filing-date parameterized.** The letter body's deadline is `filing_date + 30 calendar days`, framed as the carrier's response obligation under §541.8(b), with a tactical timeliness assertion under §541.8(a) to preempt a "you filed too late" rebuttal. `filing_date` is an optional parameter to `draft_letter()`, defaulting to `date.today()` but overridable for test reproducibility.

## Two bugs caught during the build

**Narrative path was unverified in Phase 2.** The first generated letter used a hand-written narrative placeholder, not a real LLM call. Surfaced during pre-Phase-3 verification when the question "was this LLM-generated?" was asked explicitly. Resolution: Phase 3 wired `FakeListChatModel` from `langchain_core.language_models.fake`, which actually executes the `NARRATIVE_PROMPT | llm` chain end-to-end, formats template variables, calls `llm.invoke(messages)`, and returns content. The full chain path is now exercised by every test in `test_demand_smith.py`. This was a meta-bug — the agent code was correct, but Phase 2's verification methodology was insufficient.

**Date determinism via `date.today()` leakage.** The initial Phase 2 letter showed dates partially driven by `date.today()` — meaning the letter would be non-reproducible across days and any test asserting on the 47-day window or response deadline would become flaky. Fix: all date arithmetic in `templates.build_timeliness_paragraph()` and `templates.compute_response_deadline()` now derives from explicit `filing_date` and fixture-supplied `invoice_date` / `charge_incurred_date`, never from `date.today()`. Every test passes named date constants. The letter is reproducible regardless of when it runs.

## Tool used

**Claude Code by Anthropic** — same setup as the prior two agents. VS Code terminal integration, direct file-level reads and writes, shell command execution (pytest, git).

Model used: Claude Sonnet 4.6

## Human–AI division of labor

- **Architecture decisions** (template-vs-LLM split, one-narrative-not-N, severity ordering, §541.8(b) deadline framing, zero-violations as ValueError) were human-led after Claude Code surfaced the options.
- **Honesty about the Phase 2 narrative placeholder** was Claude Code's contribution — disclosed proactively when the verification question was asked, rather than papered over.
- **Code production** within the locked architecture was Claude Code's primary contribution: the LangChain chain, the per-tone prompt injection, the seven templated sections, the severity-sort helper, the 80-test suite, the FakeListChatModel integration.
- **Bug triage** at the integration boundary was a collaboration: Claude Code surfaced both bugs (the unverified narrative path, the date-determinism leakage) and proposed structural fixes rather than special-cases.

## Verification path for a reviewer

1. Clone the repo, run `pytest tests/unit/test_demand_smith.py tests/integration/test_demand_smith.py`. Expected: 80 tests passing in under 15 seconds.
2. Read `data/schemas/letter.py` to see the `DemandLetter` model with the deadline-derivation validator and the `header_note` / `tone` interaction.
3. Read `agents/demand_smith/templates.py` to see the seven templated sections, especially `build_timeliness_paragraph()` showing the §541.8(a)/(b) dual framing.
4. Read `agents/demand_smith/prompts.py` to see the full system prompt, narrative-generation prompt, and the `TONE_INSTRUCTIONS` dict.
5. Read `agents/demand_smith/demand_smith.py` to see the `draft_letter()` orchestrator that wires template + LLM chain + assembly.

The code is the evidence. This document maps it to the bonus criteria.
