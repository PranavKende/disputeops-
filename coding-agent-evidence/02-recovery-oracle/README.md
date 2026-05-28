# RecoveryOracle — coding-agent evidence

[![Tests](https://img.shields.io/badge/tests-60_passing-brightgreen)]()
[![Framework](https://img.shields.io/badge/framework-Pydantic_AI-purple)]()
[![Stage](https://img.shields.io/badge/stage-3_(Analysis)-blue)]()

This document is the evidence record for the use of **Claude Code (Anthropic)** in building the RecoveryOracle agent — the recovery probability scorer in Stage 3 of the DisputeOps case lifecycle.

Submitted as evidence under the AgentHack 2026 +2 coding-agent bonus criteria.

## What was built

RecoveryOracle is a Pydantic AI agent that consumes a `ComplianceVerdict` (from ComplianceAuditor) plus the underlying `DisputeCase` and produces a structured `RecoveryScore` — probability of recovery, expected dollar value, recommended action (auto_file / human_review / write_off), confidence, plain-English reasoning, and the top 3-5 driving factors.

Why Pydantic AI and not LangGraph: recovery scoring is a focused single-step reasoning task with a typed input/output contract. LangGraph's graph orchestration is the wrong tool. Using a different framework here (in addition to ComplianceAuditor's LangGraph and DemandSmith's planned LangChain usage) demonstrates the "deliberate, deep usage of multiple external frameworks within a governed UiPath orchestration layer" pattern the hackathon rules reward.

| Component | Count |
|---|---|
| Pydantic AI agent | 1 |
| Pydantic v2 schemas | 1 new (`RecoveryScore`) + 1 internal (`_RecoveryScoreFromLLM`) |
| System prompt sections | 5 (role, scoring rules, positive factors, negative factors, output format) |
| Total tests | 60 |
| Lines of code (excluding tests) | ~280 |
| Test code | ~620 |

## Build phases

| Phase | Scope | Tests added | Outcome |
|---|---|---|---|
| Step A — Housekeeping | Bump pydantic-ai pin from 0.0.x to 1.x | 0 | Required: 0.x → 1.x API rename (`output_type`, `instructions`, `result.output`) |
| Phase 1 — Schemas & design | RecoveryScore model, ScoringContext, prompt structure | 0 | Four design decisions surfaced and resolved with human review |
| Phase 2 — Agent & wrapper | Pydantic AI agent, score_recovery() wrapper, full system prompt | 0 | LLM-vs-Python arithmetic split locked in |
| Phase 3 — Test suite | Unit + integration tests across 8 groups | 60 | Two real bugs surfaced (see below) |

## Four architectural decisions during Phase 1

These decisions are documented because they shaped the agent's design and would likely have been wrong if defaulted:

1. **`expected_recovery_usd` derived in Python, not LLM-computed.** LLMs hallucinate on arithmetic. The LLM produces `recovery_probability` only; the wrapper computes `round(probability × invoice.total_amount_usd, 2)` post-call. Documented in `recovery_oracle.py` as "Arithmetic isolated from LLM reasoning per agent-design discipline."

2. **Confidence clipped silently, not raised.** Schema constraint relaxed to `[0.0, 1.0]`; a `@model_validator(mode="before")` clips into `[0.6, 0.95]` before other validation. Consistent with ComplianceAuditor's pattern. Prevents Pydantic AI retry storms when the LLM consistently underestimates confidence.

3. **Low-confidence forces human_review.** A separate validator enforces that `confidence < 0.66` AND `recommended_action != "human_review"` is invalid. Catches the "low-confidence auto-file" failure mode at the schema level rather than relying on prompt instruction alone.

4. **`ScoringContext` in `recovery_oracle.py`, not `prompts.py`.** Keeps `prompts.py` as a pure constants module. ScoringContext is an agent concern (typed deps), not a prompt concern.

## Two real bugs surfaced via Claude Code

**`frozen=True` test pattern** — initial test for frozen-model immutability used `object.__setattr__` to bypass the guard, which actually works on Python 3.13 because Pydantic v2's frozen enforcement is at attribute-set, not at the lower level. Corrected to use normal attribute assignment (`score.field = x`), which raises `ValidationError` as intended. The fix is the test, not the schema.

**Borderline case_id mismatch** — the integration test's TestModel response for borderline_case was keyed to `"DEMO-BCG-2026-0003"`, but the fixture actually produces `"DEMO-BDL-2026-0003"`. Unit tests with mocked agent responses never exercised the case_id round-trip end-to-end, so the bug only surfaced in Group 7 (cross-fixture invariants). This is precisely the bug class that doesn't survive contact with downstream consumers; catching it now means RecoveryOracle's output contract holds when DemandSmith, RouteMatrix, and RecoveryPulse consume the case_id field.

## API migration: pydantic-ai 0.0.x → 1.x

Initial spec was written against pydantic-ai 0.0.13. The installed version was 1.103.0. Claude Code verified the current API before writing any code and surfaced four breaking changes:

| 0.0.x | 1.x |
|---|---|
| `result_type=...` | `output_type=...` |
| `system_prompt=...` | `instructions=...` |
| `result.data` | `result.output` |
| Sync model check at init | `defer_model_check=True` for test-time deferred init |

The phased build approach caught this at Phase 1 (design verification) rather than at Phase 2 (implementation). Skipping Phase 1 would have produced broken code against the stale API.

## Tool used

**Claude Code by Anthropic** — same setup as ComplianceAuditor. VS Code terminal integration, direct file-level reads and writes, shell command execution (pytest, git).

Model used: Claude Sonnet 4.6

## Human–AI division of labor

- **Architecture decisions** (framework choice, schema design, the four Phase 1 decisions) were human-led after Claude Code surfaced the options.
- **Pydantic AI API verification** was Claude Code's primary surface — fetching the current API documentation and identifying the 0.0.x → 1.x breaking changes.
- **Code production** within the locked architecture was Claude Code's contribution: the agent wiring, the system prompt content, the test suite across 8 groups.
- **Bug triage** at the integration boundary was a collaboration: Claude Code surfaced both bugs, proposed fixes, and verified the corrections.

## Verification path for a reviewer

1. Clone the repo, run `pytest tests/unit/test_recovery_oracle.py tests/integration/test_recovery_oracle.py`. Expected: 60 tests passing in under 10 seconds.
2. Read `data/schemas/score.py` to see the `RecoveryScore` model with the two validators (clip and low-confidence-routing).
3. Read `agents/recovery_oracle/recovery_oracle.py` to see the Pydantic AI agent initialization, the `@_agent.system_prompt` builder, and the `score_recovery()` wrapper computing `expected_recovery_usd` in Python.
4. Read `agents/recovery_oracle/prompts.py` for the full system prompt content.

The code is the evidence. This document maps it to the bonus criteria.
