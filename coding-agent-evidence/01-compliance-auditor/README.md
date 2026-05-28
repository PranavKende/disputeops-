# ComplianceAuditor — coding-agent evidence

[![Tests](https://img.shields.io/badge/tests-255_passing-brightgreen)]()
[![Framework](https://img.shields.io/badge/framework-LangGraph-purple)]()
[![Stage](https://img.shields.io/badge/stage-3_(Analysis)-blue)]()

This document is the evidence record for the use of **Claude Code (Anthropic)** in building the ComplianceAuditor agent — the FMC Part 541 rules engine in Stage 3 of the DisputeOps case lifecycle.

Submitted as evidence under the AgentHack 2026 +2 coding-agent bonus criteria.

## What was built

ComplianceAuditor is a LangGraph agent that evaluates US freight detention/demurrage invoices against 12 FMC Part 541 rules, producing a structured `ComplianceVerdict` consumed by three downstream agents in Stage 3 (RecoveryOracle, DemandSmith, RouteMatrix).

| Component | Count |
|---|---|
| Rule evaluation functions | 12 |
| LangGraph orchestration nodes | 13 (gate + 11 parallel rules + synthesize) |
| Pydantic v2 schemas | 9 (DisputeCase, Invoice, EvidencePackage, TariffReference, TerminalRecord, RuleResult, Violation, CannotEvaluate, ComplianceVerdict) |
| LLM prompts (LangChain ChatPromptTemplate) | 6 |
| FMC citations with verbatim regulatory text | 12 |
| Total tests | 255 |
| Lines of code (excluding tests) | ~2,100 |
| Test code | ~1,400 |

## Build phases

The build was structured as four phases with explicit human review at each phase boundary. Claude Code paused for greenlight before proceeding to the next phase, preventing large unreviewed changes.

| Phase | Scope | Tests added | Outcome |
|---|---|---|---|
| Step 1 — Schemas | 9 Pydantic v2 models, frozen, with model_validators | (covered by later tests) | Locked input/output contract |
| Step 2 — Citations | 12 FMC Part 541 + 545 citations with verbatim text and supporting authorities | 78 | Regulatory ground truth |
| Step 3 Phase A — Structure | Function signatures and helper signatures only, no bodies | 0 | 3 design questions surfaced for review |
| Step 3 Phase B — Deterministic rules | R001, R009, R010, R011, R012 — pure Python | 49 | Established the deterministic-rule code pattern |
| Step 3 Phase C — Mixed rules | R002, R003, R004, R005, R008 + llm_client.py | 35 + 5 | Three-helper pattern (try_deterministic / can_invoke_llm / evaluate_via_llm) |
| Step 3 Phase D — LLM rules | R006, R007 — appointment compliance and force majeure | 20 | R007 four-step incentive-function reasoning scaffold |
| Step 4 — LangGraph orchestration | StateGraph with gate, parallel fan-out, synthesize | 36 | Error isolation via `_safe_evaluate` |
| Steps 5 + 6 — Fixtures and integration tests | 3 demo-quality fixtures + 22 integration tests | 22 | Real bug surfaced (see below) |

## Real bug surfaced via Claude Code

During Step 6 integration testing, the `clean_violation_case` fixture exposed a substring-detection bug in R002's generic-phrase blocklist. The unit tests had used toy strings like `"generic"`; the integration fixture used a realistic `basis_for_charge = "Demurrage charge per tariff EMFR-2024-DEM"`. The deterministic check used full-string equality and missed the embedded `"demurrage charge"` blocklisted phrase.

Fix applied: two-tier check — exact full-string match for short tokens (`"na"`, `"n/a"`), substring match with minimum phrase length of 5 characters for multi-word boilerplate. Structurally correct, not a special case for the fixture. The next fixture with a different generic phrase will work too.

This bug would have shipped to production undetected without integration testing on realistic data. The Claude Code session that found it also produced the fix and the regression test in the same session.

## CFR corrections caught against original spec

The pre-build spec referenced FMC Part 541 with section numbers from a pre-2024 version of the regulation. During Step 2 (citations) Claude Code fetched the current eCFR text and identified three section misalignments:

| Rule | Spec said | Actual section | Why it mattered |
|---|---|---|---|
| R001 (Invoicing window) | §541.6 | §541.7(a) | Spec was citing the invoice-content subpart for a timing rule |
| R007 (Force majeure) | §541.6(b) or composite | §541.6(e)(2) + 85 FR 29638 | No discrete excepted-periods subpart exists post-2024; force majeure operates through the §541.6(e)(2) causation certification |
| R008 (Dispute notice period) | §541.7 | §541.8(a) | Spec was citing the carrier's invoice-timing subpart for the billed-party's dispute window |

Each correction is documented in the reconciliation comment block at the top of `agents/compliance_auditor/citations.py`. The corrections matter because a demand letter citing the wrong CFR subpart gives the carrier's counsel a near-free rebuttal.

## Tool used

**Claude Code by Anthropic** — the official command-line and IDE-integrated coding agent for Claude.

Sessions were run from VS Code's integrated terminal, with Claude Code reading and writing files in the repo and executing shell commands (pytest, git, file creation) directly. This direct file-level integration is meaningfully different from a copy-paste-from-chat workflow and satisfies the "meaningfully and substantively integrated" criterion in the AgentHack rules.

Model used: Claude Sonnet 4.6

## Human–AI division of labor

For precision about what Claude Code did and did not do:

- **Architecture decisions** (Track choice, agent boundaries, schema design philosophy, the four-phase build strategy, the R008 split into R008 + R012, the templated-summary decision) were human-led.
- **Regulatory research** was a collaboration: Claude Code fetched and quoted current eCFR text; the human-led review caught the section corrections against the stale spec.
- **Code review and architectural critique** happened at every phase boundary. Claude Code paused after each phase; the human approved, rejected, or adjusted before the next phase began.
- **Code production within the architecture** was Claude Code's primary contribution — function bodies, helper utilities, prompt templates, tests, and the LangGraph wiring.

This is the workflow the README documents and the codebase verifies. Cloning the repo and running `pytest` reproduces the 255-test claim in under five minutes.

## Verification path for a reviewer

A judge reviewing this evidence can verify the claim in three steps:

1. **Clone the repo** and run `pytest`. Expected: 255 tests passing in under 30 seconds.
2. **Read `agents/compliance_auditor/citations.py`** — see the CFR corrections comment block at the top and the 12 fully-cited rules with verbatim regulatory text.
3. **Read `agents/compliance_auditor/compliance_auditor.py`** — see the LangGraph StateGraph with gate, parallel fan-out, synthesize, and error isolation.

The code is the evidence. This document just maps it to the bonus criteria.
