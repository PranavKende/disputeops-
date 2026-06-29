# DisputeOps

**Agentic case management for US freight detention & demurrage dispute recovery, built on UiPath Maestro Case.**

Hackathon: **UiPath AgentHack 2026 — Track 1 (Maestro Case)**

---

## The Business Problem

US importers and shippers lose an estimated **$15.4 billion per year** in unrecovered detention and demurrage (D&D) charges — a figure documented by the Federal Maritime Commission in its 2020 D&D Study. These charges are frequently incorrect and legally disputable, but most shippers never file because disputing them is slow and painful.

The FMC's **46 CFR Part 541** rules (effective 2024) give shippers a **30-day dispute window** from the invoice date. Within that window, a shipper can formally challenge any charge that violates billing reasonableness, tariff transparency, or 12 enumerated Part 541 rules. Outside the window, the money is gone.

Processing a single D&D dispute requires extracting structured data from a carrier invoice, pulling gate timestamps from a TMS or terminal portal, cross-referencing the carrier's published tariff, evaluating 12 FMC rules, scoring recovery probability, drafting a demand letter citing specific CFR violations, and tracking the case to resolution. A mid-size BCO (Beneficial Cargo Owner) receives hundreds of invoices per month. Manual processing takes 2–3 hours per case. Most shippers triage by invoice size and abandon anything under $5,000.

DisputeOps automates this end-to-end using a Maestro Case that combines three coded agents, a low-code Agent Builder agent, document intelligence, and a human-in-the-loop approval gate — handling routine cases autonomously and escalating only uncertain or high-value disputes to a human reviewer.

---

## Case Lifecycle

The case has **6 stages**: four primary (sequential happy path), one secondary (conditional human gate), and one exception path.

```
Trigger: Google Drive FILE_CREATED → case opened (DEMO-#####)

 ┌─────────┐     ┌──────────┐     ┌──────────┐
 │  Intake  │────►│ Evidence │────►│ Analysis │
 └─────────┘     └──────────┘     └──────┬───┘
                                         │
                              ┌──────────▼──────────┐
                              │  recommended_action? │
                              └──────────┬──────────┘
                          auto_file      │      human_review
                    ┌──────────────────┘└──────────────────┐
                    ▼                                       ▼
              ┌──────────┐                          ┌──────────┐
              │  Filing  │◄─── Approved ───────────│ Approval │
              └────┬─────┘                          └────┬─────┘
                   │                     Carrier denied / write-off
                   │  carrier denied             │
                   ▼                    Rework ──►│ (loops back to Analysis)
          ┌─────────────────┐
          │ Escalated to FMC│
          └────────┬────────┘
                   │ after review → Filing
                   ▼
    ┌───────────────────────────────┐
    │ Case exits (three outcomes):  │
    │ • Resolved — Credit Issued    │ ← Filing completes  (marksCaseComplete: true)
    │ • Closed   — Written Off      │ ← Approval write-off
    │ • Closed   — Escalated to FMC │ ← Escalation stage completes
    └───────────────────────────────┘
```

### Stage 1 — Intake
**Trigger:** Google Drive `FILE_CREATED` event (polling) on the designated "DisputeOps" folder. Each new invoice PDF opens a fresh case.

**Tasks (in sequence):**
- **ChargeTagger** (Agent Builder) — classifies the charge type (detention vs. demurrage), extracts container number, and normalizes the invoice metadata.
- **InvoiceLens** (Maestro Flow + IXP) — runs the PDF through Document Understanding (IXP extraction model with a custom taxonomy) to extract all structured invoice fields: carrier, BOL number, free-time window, billed amounts, and basis-for-charge text.

### Stage 2 — Evidence
**Tasks (parallel-eligible):**
- **GateHarvester** (RPA — `workflows/gate-harvester/`) — retrieves gate-in and gate-out timestamps. In the hackathon build, returns representative data standing in for a live TMS or terminal portal call.
- **CarrierBridge** (Maestro Flow/API — `workflows/carrier-bridge/`) — retrieves the carrier's published tariff and free-time terms. In the hackathon build, returns representative data standing in for a live carrier API.

### Stage 3 — Analysis
Three coded agents run in sequence. This is the agentic core of the system.

**Tasks:**
- **ComplianceAuditor** — evaluates all 12 FMC Part 541 rules against the invoice and evidence; produces a `ComplianceVerdict` with per-rule results, an `overall_strength` label, and a summary.
- **RecoveryOracle** — scores recovery probability from the verdict; produces `recommended_action` (`auto_file` / `human_review` / `write_off`) and `expected_recovery_usd`.
- **DemandSmith** — drafts the demand letter, citing the specific CFR violations found by ComplianceAuditor, ordered by severity.

**Routing from Analysis:**
- `recommended_action == auto_file` → proceeds directly to Filing.
- `recommended_action == human_review` → routed to Approval stage for human review.
- `recommended_action == write_off` → case closed as "Written Off" (low recovery probability, not worth pursuing).

### Stage 4 — Approval *(conditional)*
Entered only when Analysis returns `human_review`.

**Task:** **ApprovalGate** (Coded Action App, React/TypeScript — `apps/approval-gate/`) — presents the ComplianceAuditor verdict, RecoveryOracle score, and DemandSmith draft letter to a human reviewer in Action Center. The reviewer sees violation count, recovery probability, total amount, and the full letter, then selects Approve, Deny, or Write Off.

**Routing from Approval:**
- `Approved` → Filing.
- `Carrier denied / rework` → loops back to Analysis stage (rework path).
- Stage completes as write-off → case exits "Closed — Written Off".

### Stage 5 — Filing
**Task:** **FilingRunner** (RPA — `workflows/filing-runner/`) — submits the demand letter through the carrier's preferred channel and registers the dispute with an FMC reference number (`FMC-<case_id>-<UTC>`). Returns an ISO timestamp confirming the filing.

**Routing from Filing:**
- Carrier issues credit → case exits "Resolved — Credit Issued" (`marksCaseComplete: true`).
- Carrier denies or goes silent / SLA breached → Escalated to FMC stage.

### Stage 6 — Escalated to FMC *(exception path)*
Entered when the carrier denies the dispute or the Filing SLA is breached.

**Task:** **EscalationReview** — a human reviews the escalation and decides whether to pursue formal FMC proceedings, refile, or close the case.

**Routing:** After review → returns to Filing, or stage completes closing the case as "Closed — Escalated to FMC".

---

## The 12 FMC Part 541 Rules

ComplianceAuditor evaluates all 12 rules on every case. Rules are grouped by implementation strategy.

### Section 1 — Deterministic (pure Python, no LLM)

| Rule | Name | CFR Basis |
|------|------|-----------|
| R001 | Invoicing window | §541.7(a) — invoice must issue within 30 calendar days of charge |
| R009 | Charge calculation | §541.6(c) — `hours_billed × hourly_rate` must equal `total_amount` (±$0.01) |
| R010 | Free time consumption | §541.6(b) — billing must not start before free time is fully consumed |
| R011 | Per-day vs per-hour rule application | §541.6(c) — demurrage must be billed per calendar day, not per hour |
| R012 | Dispute mechanism disclosure | §541.6(d) — carrier must include dispute contact/URL on invoice |

### Section 2 — Mixed (deterministic-first, LLM fallback on ambiguous cases)

| Rule | Name | CFR Basis |
|------|------|-----------|
| R002 | Required minimum content | §541.6(a)–(c) + §541.5 — all required invoice fields must be present and substantive |
| R003 | Correct billing party | §541.4 — must bill the contracting party or consignee |
| R004 | Free time accuracy | §541.6(b) — claimed free time must match the carrier's published tariff (±0.5h) |
| R005 | Gate timestamp consistency | §541.6(b) — invoice timestamps must reconcile with gate records (±15 min) |
| R008 | Dispute window status | §541.8(a) — encodes four-state window status; always `violated=False`; status is read by RecoveryOracle to inform scoring |

### Section 3 — LLM-primary (evidence gate required)

| Rule | Name | CFR Basis |
|------|------|-----------|
| R006 | Appointment compliance | §541.6(e)(2) — carrier's appointment performance; LLM determines fault attribution |
| R007 | Force majeure events | §541.6(e)(2) + 85 FR 29638 — weather or terminal closure overlapping the billed period |

Rules that cannot evaluate (missing required evidence fields) return `violated=None` and populate `missing_evidence` with field paths and sources for retrieval.

---

## UiPath Components Used

| Component | Role in DisputeOps |
|---|---|
| **Maestro Case** | Core orchestration — 6-stage case lifecycle, routing logic, case variables, three case-exit outcomes |
| **Maestro Flow** (InvoiceLens) | Document intake pipeline — triggers IXP extraction, passes file ID and structured output to the case |
| **Maestro Flow** (CarrierBridge) | API Workflow calling carrier tariff endpoint |
| **IXP (Intelligent Document Processing)** | Custom taxonomy for D&D invoice field extraction; referenced via InvoiceLens |
| **Agent Builder** (ChargeTagger) | Low-code agent for charge classification and invoice normalization at case intake |
| **UiPath Agents** (coded) | ComplianceAuditor, RecoveryOracle, DemandSmith — deployed as UiPath Agent processes to Orchestrator |
| **Action Center / Coded Action App** | ApprovalGate — human-in-the-loop review UI for uncertain cases; built in React/TypeScript |
| **RPA (Studio)** | GateHarvester (gate timestamp retrieval), FilingRunner (dispute submission and FMC reference generation) |
| **Automation Cloud / Orchestrator** | Agent process hosting, case runtime, Action Center task delivery |
| **Integration Service — Google Drive** | Case trigger: `FILE_CREATED` polling on the "DisputeOps" folder in Google Drive |

---

## Agent Architecture

DisputeOps uses **both coded agents and low-code agents**.

### Coded Agents (`agents/`)

All three are Python agents with OpenAI-compatible LLM backends (configured via `.env`):

| Agent | Framework | Role |
|---|---|---|
| **ComplianceAuditor** | LangChain 0.3 + LangGraph 0.2 | Runs a 13-node audit graph: a gate node (R002 short-circuit), 10 parallel rule nodes, and a synthesize node that produces a `ComplianceVerdict` |
| **RecoveryOracle** | Pydantic AI 1.x | `Agent(model=..., output_type=_RecoveryScoreFromLLM, deps_type=ScoringContext)` — scores recovery probability; derives `expected_recovery_usd` in Python post-LLM to avoid arithmetic hallucination |
| **DemandSmith** | LangChain 0.3 (ChatPromptTemplate) | LLM generates one prose paragraph (the opening argument); all letterhead, CFR citations, dollar amounts, and structure are assembled deterministically from templates |

A fourth agent, **PaperTrail** (`agents/paper_trail/`), handles PDF and email evidence extraction using `pdfplumber`. It is part of the codebase but not wired into the current case as a live task — it serves as the Python-layer evidence extractor for the coded-agent test fixtures.

### Low-Code Agent

**ChargeTagger** — built in UiPath Agent Builder (cloud-only; no local source in this repo). Handles charge classification at the Intake stage.

---

## What's Real vs. Representative

### Real (live agentic reasoning)

- **InvoiceLens / IXP** — genuine document intelligence extraction on actual invoice PDFs, using a trained taxonomy in Document Understanding.
- **ChargeTagger** — real Agent Builder agent classifying charge types from invoice text.
- **ComplianceAuditor** — 12 live FMC rule evaluations; 5 deterministic, 5 mixed (LLM fallback on ambiguous cases), 2 LLM-primary. Evaluates every case and produces structured per-rule results with citations, evidence refs, and confidence scores.
- **RecoveryOracle** — live LLM scoring of recovery probability from the compliance verdict; routes the case.
- **DemandSmith** — live LLM letter drafting from the compliance and recovery outputs.
- **ApprovalGate** — real Coded Action App surfacing the full verdict, score, and draft letter to a human reviewer in Action Center.

### Representative (demo data, not live integrations)

- **GateHarvester** — returns hard-coded representative gate-in/gate-out timestamps in place of a live TMS or terminal portal API call. In production, this would call the shipper's TMS or scrape a terminal operator's web portal.
- **CarrierBridge** — returns representative tariff and free-time data in place of a live carrier API or tariff database. In production, this would call the carrier's portal, an EDI tariff feed, or a third-party tariff aggregator.
- **FilingRunner** — generates a formatted FMC reference number (`FMC-<case_id>-<UTC>`) and ISO timestamp confirming the filing, but does not submit to a live carrier portal or EDI endpoint. The filing output is real enough for the dispute record; the channel integration is the remaining production gap.

---

## Repository Structure

```
disputeops/
├── agents/
│   ├── compliance_auditor/    LangChain + LangGraph: 12-rule FMC audit graph
│   ├── recovery_oracle/       Pydantic AI: recovery probability scoring
│   ├── demand_smith/          LangChain: demand letter drafting
│   ├── paper_trail/           pdfplumber + LangChain: evidence extraction (not in case)
│   └── shared/                LLM client factory, shared exceptions
├── case/
│   ├── caseplan.json          Maestro Case definition — 6 stages, v1.0.2, confirmed running
│   ├── case-plan.json         Alternate rendering (same content)
│   ├── bindings_v2.json       Solution resource bindings
│   ├── entry-points.json
│   ├── sdd.md                 Solution Design Document (source of truth for the case spec)
│   └── tasks.md               Task registry (T01–T59) used during case authoring
├── flows/
│   └── invoice-lens/
│       └── InvoiceLens.flow   Maestro Flow source — invoice PDF intake + IXP extraction
├── apps/
│   └── approval-gate/         React/TypeScript Coded Action App (Vite)
│       └── src/
│           ├── components/Form.tsx   Verdict display + decision UI
│           └── uipath.ts             UiPath Action Center service integration
├── workflows/
│   ├── filing-runner/         RPA: Main.xaml — dispute submission + FMC ref generation
│   ├── gate-harvester/        RPA: Main.xaml — gate timestamp retrieval
│   └── carrier-bridge/        Maestro Flow: carrier tariff and free-time lookup
├── uipath/
│   └── document_understanding/
│       └── invoice_lens_taxonomy.json   IXP field taxonomy for invoice extraction
├── data/
│   └── schemas/               Python: DisputeCase, ComplianceVerdict, RecoveryScore, DemandLetter
├── tests/
│   ├── unit/                  Pure Python tests (all LLM calls mocked)
│   └── integration/           End-to-end agent tests (fixture-level LLM mocks)
├── .env.example               Required environment variables (copy to .env, add real keys)
├── pyproject.toml
├── LICENSE
└── README.md
```

---

## Setup

**Prerequisites:** Python 3.11+, an OpenAI API key (or compatible endpoint).

```bash
# 1. Clone and install
git clone <repo-url>
cd disputeops
python -m venv .venv
.venv\Scripts\activate       # Windows
pip install -e ".[dev]"

# 2. Configure environment
copy .env.example .env
# Edit .env — set OPENAI_API_KEY and optionally per-agent model overrides

# 3. Run the unit tests (no API key needed — all LLM calls are mocked)
pytest tests/unit/

# 4. Run integration tests (requires a valid OPENAI_API_KEY — calls are mocked at fixture level)
pytest tests/integration/
```

**To run a specific agent directly:**

```python
from agents.compliance_auditor.compliance_auditor import run_audit
from data.fixtures.clean_violation_case import make_clean_violation_case

verdict = run_audit(make_clean_violation_case())
print(verdict.overall_strength, verdict.violations)
```

The Maestro Case itself runs on UiPath Automation Cloud (staging.uipath.com). To trigger a live case run, drop a freight invoice PDF into the configured Google Drive folder.

---

## Test Suite

348 test methods across 11 test files (7 unit, 4 integration).

| File | Tests | Covers |
|---|---|---|
| `unit/test_rules.py` | 67 | All 12 rule functions — deterministic paths, LLM-fallback branches, `cannot_evaluate` returns |
| `unit/test_demand_smith.py` | 55 | Letter assembly, severity ordering, template rendering, CFR citation inclusion |
| `unit/test_recovery_oracle.py` | 42 | Scoring thresholds, `recommended_action` routing, `expected_recovery_usd` derivation |
| `unit/test_paper_trail.py` | 42 | PDF field extraction, email parser, evidence normalization |
| `unit/test_citations.py` | 23 | Citation registry — 12 rules have verbatim CFR text, cross-references valid |
| `unit/test_compliance_auditor.py` | 33 | Graph structure, normal path, R002 short-circuit, error isolation, `overall_strength` labels |
| `unit/test_llm_client.py` | 5 | LLM client factory configuration |
| `integration/test_demand_smith.py` | 20 | Full letter drafts from the three demo fixtures |
| `integration/test_paper_trail.py` | 23 | PDF extraction against the three sample invoices |
| `integration/test_recovery_oracle.py` | 16 | Scoring from the three demo fixtures; fixture-level LLM mocks |
| `integration/test_compliance_auditor.py` | 22 | End-to-end audit graph against three fixtures with mocked LLM calls |

All LLM calls are intercepted by mocks in both unit and integration tests — no real API key is required to run the suite.

---

## Submission Status

| Component | Status |
|---|---|
| Maestro Case (6 stages) | Deployed and run to `finalStatus: Completed` on Automation Cloud (debug build, 2026-06-23) |
| ComplianceAuditor | Deployed to Orchestrator as `compliance-auditor-agent-v2`; invoked live in completed case run |
| RecoveryOracle | Deployed to Orchestrator as `recovery-oracle-agent-v2`; invoked live in completed case run |
| DemandSmith | Deployed to Orchestrator as `demand-smith-agent-v2`; invoked live in completed case run |
| InvoiceLens (IXP) | Deployed as Maestro Flow in `Shared/IXP_invoiceLens`; runs Document Understanding extraction |
| ChargeTagger | Deployed via Agent Builder (`Shared/ChargeTagger`) |
| ApprovalGate | Deployed as Coded Action App (`Shared.dispute-approval-app`); Action Center task delivery confirmed |
| GateHarvester / CarrierBridge | Deployed to `Shared/Hackathon-DisputeOps`; return representative data |
| FilingRunner | Deployed to `Shared/Hackathon-DisputeOps`; returns FMC ref + ISO timestamp |
| EscalationReview | Deployed (cloud-only; source not included in this repo) |

---

## The Coding-Agent Story

DisputeOps was built using **Claude Code with UiPath's CLI skills** (`uip`). The coding-agent story is part of the submission.

**What the agent did:**

- Scaffolded the Maestro Case plan from the solution design document (`case/sdd.md`), producing the initial `caseplan.json` with 6 stages, task definitions, variable declarations, entry/exit conditions, and routing rules — without manually opening Studio Web.

- Caught and fixed a real case-completion bug. The original design had three mutually exclusive terminal outcomes (Credit Issued, Written Off, Escalated to FMC) but specified that all three must complete before the case could close. This made case completion structurally impossible. The fix: each `caseExitRule` checks for `selected-stage-completed` on its own stage only — so whichever terminal stage fires first closes the case.

- Diagnosed a data-contract mismatch between InvoiceLens IXP output and ComplianceAuditor's Pydantic schema (8 field binding errors), and designed the wrapping strategy that resolved it (Path C: four sibling inputs consolidated into one `case` object via `=js:({})`).

- Investigated InvoiceLens and chose to implement it as a Maestro Flow rather than an RPA sequence — the Flow triggers natively from the Google Drive event, passes the file ID directly into IXP, and returns structured output back to the case without requiring an unattended robot session.

- Rewrote and republished FilingRunner after diagnosing that the original version's output contract didn't match the case variable bindings; verified the fix with a live Orchestrator job run.

- Tracked down the case-stage entry rule bug where the entry condition used `selected-stage-exited` instead of `selected-stage-completed` — causing the Analysis stage to never fire because `stagesExited` was always empty (stages that complete with `marksStageComplete: true` are recorded as completions, not exits).

---

## License

MIT License — Copyright © 2026 Pranav Kende. See `LICENSE`.
