# Coding-Agent Evidence

[![460 tests passing](https://img.shields.io/badge/all_agents-460_tests_passing-brightgreen)]()
[![Hackathon: AgentHack 2026](https://img.shields.io/badge/Hackathon-UiPath_AgentHack_2026-blue)]()
[![Track: Maestro Case](https://img.shields.io/badge/Track-Maestro_Case-purple)]()

This folder documents the use of Claude Code (Anthropic) during DisputeOps development. It is submitted as evidence for the **+2 bonus** awarded under UiPath AgentHack 2026 rules for documented coding-agent use.

## Bonus claim

This folder provides multiple forms of verifiable evidence per the AgentHack 2026 rules for the 2-point bonus tier: the original prompts submitted to Claude Code (`prompts/`), curated session summaries describing what was built (`session.md`), screenshots of key Claude Code interactions (`screenshots/`), and a per-session changelog with file counts, test counts, and bugs found (`changelog.md`).

The rules require: (a) the name of the coding agent used, (b) how it contributed, and (c) at least one form of verifiable evidence. This folder supplies all three, with multiple evidence types per agent.

## Tool used

**Claude Code** by Anthropic — the official command-line and IDE-integrated coding agent for Claude. Sessions were run from VS Code's integrated terminal, with Claude Code reading and writing files in the repo and executing shell commands (pytest, git, file creation) directly. This direct file-level integration distinguishes the workflow from a copy-paste-from-chat pattern and satisfies the "meaningfully and substantively integrated" criterion in the rules.

**Model used**: `Claude Sonnet 4.6`

## Outcomes — All four coded agents (Stages 2 + 3, complete)

| Agent | Framework | Tests |
|---|---|---|
| ComplianceAuditor | LangGraph | 255 |
| RecoveryOracle | Pydantic AI | 60 |
| DemandSmith | LangChain | 80 |
| PaperTrail | LangChain | 65 |
| **Total** | | **460** |

| Metric | Value |
|---|---|
| Total tests passing | 460 |
| Real bugs found and fixed | 6 across four agents (R002 substring-detection; clip_confidence boundary; expected_recovery_usd LLM-vs-Python split; tone selection edge case; BOL regex VERBOSE-mode failure; tariff two-pattern extraction) |
| CFR corrections caught against spec | 3 (R001 §541.6 → §541.7(a); R007 §541.6(b) → §541.6(e)(2); R008 §541.7 → §541.8(a)) — verified against the current eCFR after the 2024 Part 541 rewrite (89 FR 14330) |
| External frameworks integrated | LangGraph, LangChain, Pydantic AI, Pydantic v2, pytest |
| Lines of code produced | ~7,000 (all agents + schemas + tests, estimated) |
| Build duration | ~6 days across multiple Claude Code sessions |

## What Claude Code did NOT do

To be precise about the human–AI division of labor on this build:

- **Architecture decisions were human-led.** Track choice (Maestro Case over BPMN), agent boundary design (four coded agents plus one low-code agent vs. a monolith), schema design philosophy (frozen Pydantic v2, separate `CannotEvaluate` model rather than a nullable `violated` field), and the four-phase build strategy were all decided by the human developer (Pranav Kende, the submitting entrant).

- **Regulatory research was a collaboration with human-led review.** Claude Code fetched and quoted the CFR text, but the human-led review identified that the original spec's section numbers were stale against the 2024 Part 541 rewrite. Two of the three CFR corrections originated from Claude Code's fact-finding; one originated from the human review of Claude Code's draft.

- **Code review and architectural critique happened at every phase boundary.** Claude Code was instructed to pause after each phase. The human reviewed the output, approved or rejected the design, surfaced edge cases (e.g., the R008 status-reporter split into R008 + R012; the R011 not-applicable semantics; the `_build_summary` templated-vs-LLM decision), and adjusted the spec before the next phase began.

- **The hackathon strategy was human-led.** Track selection, problem statement (freight detention/demurrage recovery), agent naming, and the demo narrative were not authored by Claude Code.

Claude Code's role was high-leverage code production within a human-defined architecture, with phase-gated review. This is the workflow the README documents and the evidence supports.

## What's in this folder

Each subfolder corresponds to one agent or component in the DisputeOps system:

| Subfolder | Agent / Component | Status |
|---|---|---|
| `01-compliance-auditor/` | ComplianceAuditor (LangGraph, Stage 3) | ✅ Complete |
| `02-recovery-oracle/` | RecoveryOracle (Pydantic AI, Stage 3) | ✅ Complete |
| `03-demand-smith/` | DemandSmith (LangChain, Stage 3) | ✅ Complete |
| `04-paper-trail/` | PaperTrail (LangChain, Stage 2) | ✅ Complete |
| `05-simulator/` | Case simulator and orchestration harness | ⏳ Planned |
| `06-uipath-integration/` | Maestro Case wiring, Action Center forms, DMN tables | ⏳ Planned |

## What each subfolder contains

- `session.md` — curated session-by-session summary of what was built, prompts used, and outcomes
- `prompts/` — the exact prompts submitted to Claude Code at each phase
- `screenshots/` — screenshots of Claude Code sessions at key milestones
- `changelog.md` — per-session diff summary (files created, tests added, bugs found and fixed)

> **Note on raw session files**: raw Claude Code session JSON exports (`raw_session_*.json`) are excluded from this repo via `.gitignore`. They contain full conversation transcripts which can be large and may include incidental local paths. Only the curated `session.md` summaries are committed.

## How a reviewer can verify the bonus claim

A judge reviewing this submission can verify the +2 bonus criteria as follows:

1. **Tool identification**: see the "Tool used" section above.
2. **Contribution description**: see the "Outcomes" and "What Claude Code did NOT do" sections.
3. **Verifiable evidence**: open any populated subfolder (starting with `01-compliance-auditor/`) and review the `prompts/`, `session.md`, `screenshots/`, and `changelog.md` artifacts.
4. **Code-level verification**: the test count claimed in this README is reproducible — clone the repo, install dependencies per the top-level `README.md`, and run `pytest`. The 460-test claim is verifiable in under five minutes.

## Submission context

- **Hackathon**: UiPath AgentHack 2026
- **Track**: 1 — UiPath Maestro Case
- **Submitter**: Pranav Kende (solo entrant)
- **Project repository**: this repo (`disputeops`)
- **Submission deadline**: June 29, 2026, 11:45 pm EDT

---

*This document is part of the official AgentHack 2026 submission for DisputeOps. The MIT license at the repo root applies to all original solution code. UiPath proprietary tools and frameworks remain subject to their own license terms per the hackathon rules.*