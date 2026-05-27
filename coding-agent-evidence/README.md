# Coding-Agent Evidence

This folder documents the use of Claude Code (Anthropic) during DisputeOps development.
It is submitted as evidence for the **+2 hackathon bonus** awarded for documented coding-agent use
per UiPath AgentHack 2026 rules.

## Bonus claim

Per AgentHack 2026 rules, teams earn +2 points for demonstrating that a coding agent was used
in the development of their submission. This folder provides verifiable, per-agent evidence
including the original prompts, session summaries, and observable outcomes (tests written,
bugs found, code produced).

## What's in this folder

Each subfolder corresponds to one agent or component in the DisputeOps system:

| Subfolder | Agent / Component | Status |
|-----------|-------------------|--------|
| `01-compliance-auditor/` | ComplianceAuditor (LangGraph, Stage 3) | ✅ Complete |
| `02-recovery-oracle/` | RecoveryOracle (Pydantic AI, Stage 3) | ⏳ Planned |
| `03-demand-smith/` | DemandSmith (LangChain, Stage 3) | ⏳ Planned |
| `04-paper-trail/` | PaperTrail (LangChain, Stage 2) | ⏳ Planned |
| `05-simulator/` | Case simulator and orchestration harness | ⏳ Planned |
| `06-uipath-integration/` | Maestro Case wiring, Action Center, DMN | ⏳ Planned |

## What each subfolder contains

- `session.md` — curated session-by-session summary of what was built, prompts used, and outcomes
- `prompts/` — the exact prompts submitted to Claude Code at each phase
- `screenshots/` — screenshots of Claude Code sessions at key milestones
- `changelog.md` — per-session diff summary (files created, tests added, bugs found/fixed)

> **Note on raw session files:** Raw Claude Code session JSON exports (`raw_session_*.json`)
> are excluded from this repo via `.gitignore` — they contain full conversation transcripts
> which can be large. Only the curated `session.md` summaries are committed.

## Tool used

**Claude Code** by Anthropic — the official CLI for Claude, running as an interactive
coding agent in VS Code. Model: `claude-sonnet-4-6`.

All four build phases of ComplianceAuditor (deterministic rules → mixed rules → LLM rules →
LangGraph orchestration) were executed entirely through Claude Code sessions, producing
fully-tested, production-quality Python code with structured Pydantic schemas, LangChain
prompts, and pytest suites totalling 255 tests.
