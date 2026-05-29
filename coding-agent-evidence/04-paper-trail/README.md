# PaperTrail — coding-agent evidence

[![Tests](https://img.shields.io/badge/tests-65_passing-brightgreen)]()
[![Framework](https://img.shields.io/badge/framework-LangChain-purple)]()
[![Stage](https://img.shields.io/badge/stage-2_(Evidence_Gathering)-blue)]()

This document is the evidence record for the use of **Claude Code (Anthropic)** in building the PaperTrail agent — the document extractor in Stage 2 of the DisputeOps case lifecycle.

Submitted as evidence under the AgentHack 2026 +2 coding-agent bonus criteria.

## What was built

PaperTrail is a LangChain agent that takes a freight invoice in one of three input forms (PDF, email body, EDI 210) and produces a fully-populated `DisputeCase` Pydantic object for the three Stage 3 reasoning agents to consume. It is the upstream extractor that the rest of the system depends on.

Why LangChain (not LangGraph, not Pydantic AI): document extraction is structured-output-from-unstructured-input — a single LLM call per document with `with_structured_output(Invoice)`. LangChain's prompt-template + Pydantic-output pattern is the natural fit. This is the second agent in the project to use LangChain (after DemandSmith), reinforcing the "deliberate framework diversity" pattern: LangGraph for orchestration, Pydantic AI for typed single-call reasoning, LangChain for prose-and-extraction tasks.

| Component | Count |
|---|---|
| LangChain agent | 1 |
| Pydantic v2 sources (discriminated union) | 3 (PdfSource, EmailSource, EdiSource) |
| Regex extractors | 11 patterns (dollar amount, ISO date, US long date, container number, BOL number, invoice number, hours billed, hourly rate, tariff reference, free time days, total amount label-anchored) |
| PDF generation library (test infrastructure) | fpdf2 |
| PDF extraction library | pdfplumber |
| Synthetic test PDFs | 3 (one per fixture) |
| Total tests | 65 |
| &nbsp;&nbsp;— Unit | 42 |
| &nbsp;&nbsp;— Integration | 23 |
| Lines of code (excluding tests and PDF generator) | ~1,020 |
| Test code | ~890 |

## The hybrid extraction architecture (the key design discipline)

PaperTrail is the fourth agent in DisputeOps where template-vs-LLM discipline shaped the architecture. The pattern: deterministic extractors handle fields with regular structure; LLM handles unstructured fields; both paths cross-check each other.

**Regex-extracted (deterministic, 10 of 14 Invoice fields)**:
- `invoice_number`, `bol_number`, `container_number`, `total_amount_usd`, `invoice_date`, `charge_incurred_date`, `hours_billed`, `hourly_rate_usd`, `free_time_start`/`end` (partial), tariff reference

**LLM-extracted (prose, 4 of 14 Invoice fields)**:
- `carrier_name` (free-form letterhead)
- `billed_to_party` (varies in position and label)
- `charge_type` (classification: detention | demurrage | tonu | layover | lumper | other)
- `basis_for_charge` (the carrier's narrative reason)

**Cross-check pattern**: The LLM prompt receives the regex-extracted fields as "confirmed context" and is instructed to confirm or correct. When the LLM disagrees with the regex on a numeric field, a WARNING is logged and the LLM value wins (the cross-check catches OCR errors and false-positive regex matches). The warning surfaces disagreements for human review without blocking extraction.

This split is what makes PaperTrail both reliable (regex never hallucinates a container number) and flexible (LLM handles the prose variance that no regex could).

## EvidencePackage scope — an architectural decision

PaperTrail only populates EvidencePackage fields that appear in the source document. This decision matters for system architecture:

| Field | Populated by PaperTrail? | Source in real Maestro execution |
|---|---|---|
| carrier_tariff_reference (tariff_number) | ✅ from invoice | Invoice |
| carrier_tariff_reference (effective_date) | ❌ stays None | CarrierBridge (API call to tariff service) |
| carrier_tariff_reference (published_free_time_hours) | ❌ stays None | CarrierBridge (API call to tariff service) |
| published_free_time_days | ✅ from invoice | Invoice |
| bol_terms | ✅ from invoice (if printed) | Invoice + GateHarvester |
| shipper_name | ✅ from invoice | Invoice |
| gate_in_timestamp / gate_out_timestamp | ❌ stays None | GateHarvester (TMS scrape) |
| appointment_time | ❌ stays None | GateHarvester or terminal portal |
| terminal_records | ❌ stays None | CarrierBridge |
| weather_events | ❌ stays None | External weather API |

This is correct system design. PaperTrail does one job (document extraction) and surfaces gaps honestly. The R004 rule in ComplianceAuditor will return `cannot_evaluate` when tariff details are missing, with `missing_evidence` pointing to where the data can be fetched. This is the agentic case management pattern — agents that surface gaps rather than guess.

## Build phases

| Phase | Scope | Tests added | Outcome |
|---|---|---|---|
| Step A — Housekeeping | Add fpdf2 dev dependency, confirm pdfplumber, add PAPER_TRAIL_MODEL env var | 0 | Dependencies pinned and reproducible |
| Phase 1 — Design | Discriminated-union source dispatch, 11 regex patterns, LLM cross-check pattern, EvidencePackage scope decision | 0 | Five design questions surfaced and resolved |
| Phase 2 — Implementation | Extractors, prompts, agent, synthetic PDF generator, round-trip verification | 0 | 15/15 Invoice fields round-tripped through fixture → PDF → extraction |
| Phase 3 — Test suite | 11 test groups across unit + integration | 65 | Six real bugs surfaced and fixed |

## Six real bugs caught during test authoring

PaperTrail had more bugs surface during testing than any other agent in DisputeOps — not because the agent is buggier, but because document extraction has more failure surface (PDF encoding, regex precision, MIME parsing, library compatibility). Each fix is structural, not a special case.

1. **VERBOSE-mode regex `#` comment delimiter conflict.** The `_BOL_LABELED` and `_INVOICE_NUMBER` patterns used the `re.VERBOSE` flag for readability, but `#` is a comment delimiter in VERBOSE mode and the pattern contained a literal `#`. Compilation failed at import time. Fixed by removing VERBOSE from both patterns.

2. **Tariff false-positive on "Tariff Reference" header.** The pattern `(?:tariff)\s+([A-Z]{2,}\d[\w\-]+)` required a letter-then-digit transition; `EMFR-2024-DEM` starts with letters not immediately followed by a digit, so the match failed. Fixed with a two-pattern strategy: labeled "Tariff Reference: tariff X" primary, inline "per tariff X" fallback with digit-containment.

3. **Free time pattern word-order assumption.** The original pattern required "N days free time" but real invoices write "Published Free Time: N days". Added a labeled alternative.

4. **fpdf2 em-dash encoding failure.** The character `—` is outside Helvetica's latin-1 range. Synthetic PDF generation failed at the encoding boundary. Replaced with `--` in the generator.

5. **FakeListChatModel incompatibility with `with_structured_output`.** LangChain's `FakeListChatModel` returns string content, but `with_structured_output` expects the model to support structured outputs natively. Integration tests patch `_invoke_llm` directly (returning the structured Pydantic model). Unit tests that verify the chain construction use `RunnableLambda` to make the mock a proper LangChain Runnable. This is a documented LangChain gotcha worth knowing for any future agent in the project.

6. **`temperature=0.0` not explicitly passed.** The `_invoke_llm` function relied on `get_llm_client`'s default temperature. The engineering standard requires explicit temperature for determinism. Made it explicit.

The first three are pattern-precision bugs — exactly the failure mode where synthetic data passes and real-world data fails. Catching them inside the test suite (rather than during the demo) is what integration testing exists for.

## Tool used

**Claude Code by Anthropic** — same setup as the prior three agents. VS Code terminal integration, direct file-level reads and writes, shell command execution (pytest, git, Python PDF generation).

Model used: Claude Sonnet 4.6

## Human–AI division of labor

- **Architecture decisions** (hybrid regex+LLM split, discriminated-union source dispatch, EvidencePackage scope, case_id_override pattern for test parity) were human-led after Claude Code surfaced the options.
- **Regex pattern authoring** was a collaboration: Claude Code proposed patterns based on the synthetic PDF format; the test suite found three precision bugs that required pattern revision.
- **Code production** within the locked architecture was Claude Code's primary contribution: the regex patterns, the dispatch logic, the LangChain chain, the synthetic PDF generator, and the 65-test suite.
- **Bug triage** during test authoring was a collaboration: Claude Code surfaced each of the six bugs, proposed structural fixes (not special cases), and verified the corrections.

## Verification path for a reviewer

1. Clone the repo, run `pytest tests/unit/test_paper_trail.py tests/integration/test_paper_trail.py`. Expected: 65 tests passing in under 20 seconds.
2. Run `python tools/generate_test_documents.py` (if not already run) to produce the three synthetic PDFs in `tests/fixtures/documents/`.
3. Read `agents/paper_trail/extractors/patterns.py` to see the 11 regex patterns with their docstring examples.
4. Read `agents/paper_trail/paper_trail.py` to see the discriminated-union dispatch and the `extract_case()` orchestrator.
5. Read `agents/paper_trail/prompts.py` to see the LLM cross-check prompt.

The code is the evidence. This document maps it to the bonus criteria.
