# DisputeOps

**Agentic case management for US freight detention/demurrage dispute recovery, built on UiPath Maestro Case.**

Hackathon: **UiPath AgentHack 2026 — Track 1 (Maestro Case)**

![Build](https://img.shields.io/badge/build-passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Tests](https://img.shields.io/badge/tests-255%20passing-brightgreen)

---

## The problem

Every year, US importers and shippers lose an estimated **$15.4 billion** in unrecovered detention and demurrage charges — a figure documented by the Federal Maritime Commission (FMC) in its 2020 D&D Study. The charges are often incorrect, often disputable, and almost always ignored because disputing them is slow and painful.

The FMC's Part 541 rules give shippers a **30-day dispute window** from the date of invoice. Inside that window, a shipper can formally challenge any charge that violates billing reasonableness, tariff transparency, or the FMC's 12 enumerated Part 541 rules. Outside that window, the money is gone.

### Why most shippers can't recover

Processing a single detention dispute requires:

1. Extracting structured data from a carrier invoice (PDF, EDI, or portal HTML)
2. Pulling gate-in/gate-out timestamps from a TMS or terminal operator website
3. Cross-referencing tariff publications and published free-time terms
4. Evaluating the charge against 12 FMC Part 541 rules — a mix of bright-line tests and contextual judgment calls
5. Scoring recovery probability before investing attorney hours
6. Drafting a demand letter that cites specific FMC rule violations
7. Filing the dispute through the carrier's preferred channel (portal, email, or EDI)
8. Tracking the case to resolution

A mid-size BCO (Beneficial Cargo Owner) receives hundreds of detention invoices per month. Processing each dispute manually takes 2–3 hours. Most shippers triage by invoice size and abandon anything under $5,000 — leaving large volumes of small, legitimate claims on the table.

### Why this is a case-management problem, not a BPMN problem

Detention disputes are **path-emergent** and **exception-heavy**. The carrier may respond with a partial credit, a counter-dispute, a tariff revision, or silence. Stage 3 analysis may find zero violations (case closed) or seven violations requiring different legal theories. The human gate may escalate, approve, or reject.

A linear BPMN workflow can't handle this. What's needed is a **case** — a persistent, stateful work item with lifecycle stages, multiple actors, parallel sub-tasks, and a governed decision point. UiPath Maestro Case provides exactly this model.

---

## Solution overview

DisputeOps implements a **five-stage Maestro Case lifecycle**:

```
Intake → Evidence Gathering → Analysis → Human Gate → File & Track
```

| Stage | Name | What happens |
|-------|------|--------------|
| 1 | Intake | Invoice received; IDP extracts fields; ChargeTagger classifies charge type |
| 2 | Evidence Gathering | TMS scrape for gate timestamps; carrier portal lookup for tariff terms |
| 3 | Analysis | FMC Part 541 compliance audit; recovery probability score; demand letter draft |
| 4 | Human Gate | Manager reviews ComplianceAuditor findings; approves, rejects, or escalates |
| 5 | File & Track | FilingRunner files the dispute; RecoveryPulse tracks to resolution |

The build comprises **13 components** (12 agents/tools plus the Maestro Case shell). See [docs/architecture.md](docs/architecture.md) for the full architecture diagram — ⏳ will be added in Week 2.

---

## UiPath components used

| Component | Role in DisputeOps |
|-----------|-------------------|
| **UiPath Maestro Case** | Orchestration spine — provides the stateful case lifecycle, stage routing, and SLA enforcement |
| **UiPath Studio Web** | Case stage design, BPMN within stages, and case schema configuration |
| **UiPath Data Fabric** | Case object persistence — stores invoice data, evidence, audit results, and case history |
| **UiPath Document Understanding** | Invoice field extraction in Stage 1 (InvoiceLens) — handles PDFs, images, and EDI documents |
| **UiPath Agent Builder** | Low-code charge classification agent (ChargeTagger) in Stage 1 |
| **UiPath API Workflows** | Carrier portal lookups in Stage 2 (CarrierBridge) |
| **UiPath RPA bots** | TMS gate-timestamp scraping (GateHarvester, Stage 2) and dispute filing (FilingRunner, Stage 5) |
| **UiPath Action Center** | Human approval gate in Stage 4 — routes ComplianceAuditor findings to a manager |
| **UiPath DMN** | RouteMatrix decision table in Stage 3 — routes to human review or auto-file based on confidence score |
| **UiPath Process Intelligence** | RecoveryPulse telemetry dashboard in Stage 5 — tracks filed disputes, recovery rates, and cycle times |
| **UiPath Automation Cloud** | Deployment target for all agents, bots, and Maestro Case |

---

## Agent type statement

DisputeOps uses **both coded and low-code agents**.

The Stage 1 **ChargeTagger** is a low-code UiPath Agent Builder agent. **ComplianceAuditor**, **RecoveryOracle**, **DemandSmith**, and **PaperTrail** are coded agents — implemented in Python using LangGraph, Pydantic AI, and LangChain, then deployed via UiPath's coded-agent SDK. The Maestro Case orchestrates all agent types within a single governed lifecycle.

---

## Agents in the system

| Agent | Framework | Stage | Role | Status |
|-------|-----------|-------|------|--------|
| InvoiceLens | UiPath Document Understanding | 1 | Extracts invoice fields from PDFs and EDI | ⏳ Planned |
| ChargeTagger | UiPath Agent Builder | 1 | Classifies charge type (detention, demurrage, per diem) | ⏳ Planned |
| GateHarvester | UiPath RPA | 2 | Scrapes TMS for container gate-in/gate-out timestamps | ⏳ Planned |
| CarrierBridge | UiPath API Workflow | 2 | Calls carrier portal API for tariff terms and free time | ⏳ Planned |
| PaperTrail | LangChain + coded | 2 | Parses PDFs and email threads for supporting evidence | 🚧 In Progress |
| **ComplianceAuditor** | **LangGraph** | **3** | **Evaluates 12 FMC Part 541 rules; returns structured verdict** | **✅ Built** |
| RecoveryOracle | Pydantic AI | 3 | Scores recovery probability using case features | 🚧 In Progress |
| DemandSmith | LangChain + coded | 3 | Drafts dispute demand letter citing specific rule violations | 🚧 In Progress |
| RouteMatrix | UiPath DMN | 3 | Routes case to human review or auto-file based on score | ⏳ Planned |
| ApprovalGate | UiPath Action Center | 4 | Manager reviews and approves, rejects, or escalates | ⏳ Planned |
| FilingRunner | UiPath RPA | 5 | Files dispute via carrier portal or email | ⏳ Planned |
| RecoveryPulse | UiPath Process Intelligence | 5 | Recovery telemetry dashboard | ⏳ Planned |

---

## ComplianceAuditor — built and tested

ComplianceAuditor is the analytical core of Stage 3. Given a structured detention/demurrage case, it evaluates the invoice against all 12 FMC Part 541 rules and returns a structured verdict with per-rule findings, applicable citations, and an overall recommendation.

### The 12 rules evaluated

| Rule | Name | Type |
|------|------|------|
| R001 | Free Time Adequacy | Deterministic |
| R002 | Invoice Timing | Mixed (LLM fallback) |
| R003 | Tariff Transparency | LLM-assisted |
| R004 | Dispute Process Clarity | LLM-assisted |
| R005 | Billing Reasonableness | LLM-assisted |
| R006 | Notice Requirements | LLM-assisted |
| R007 | Force Majeure Recognition | LLM-assisted |
| R008 | Container Availability | Deterministic |
| R009 | Free Time Extension | Deterministic |
| R010 | Cargo Availability Notification | Deterministic |
| R011 | Equipment Interchange Receipt | Deterministic |
| R012 | Invoicing Party Identity | Deterministic |

### Architecture

```
ComplianceAuditorAgent (LangGraph)
├── RulesEngine
│   ├── DeterministicRules (R001, R008–R012)     — pure Python, no LLM
│   ├── MixedRules (R002)                         — Python primary, LLM fallback
│   └── LLMRules (R003–R007)                      — structured LangChain prompts
├── CitationsEngine                               — maps findings to FMC CFR citations
└── Schemas (Pydantic v2)
    ├── DetentionCase                             — input model
    └── ComplianceVerdict                         — output model
```

### Test coverage

```
255 tests — all passing
├── 78  citation tests
├── 49  deterministic rule tests
├── 35  mixed rule tests
├── 20  LLM rule tests (mocked)
├── 36  orchestration tests
├── 5   LLM client tests
├── 22  integration tests
└── 10  cross-cutting tests
```

---

## Coding agents used

**Tool: Claude Code (Anthropic)**

ComplianceAuditor was built entirely through Claude Code sessions across four phases:

| Phase | Scope | Outcome |
|-------|-------|---------|
| 1 — Deterministic rules | R001, R008–R012; Pydantic schemas; citation engine | 127 tests passing |
| 2 — Mixed rules | R002 with substring-detection bug found and fixed | +35 tests |
| 3 — LLM rules | R003–R007 via structured LangChain prompts; mock harness | +20 tests |
| 4 — LangGraph orchestration | Full agent graph; integration tests; cross-cutting tests | 255 tests total |

The R002 substring-detection bug (partial carrier name matching) was found during integration testing through Claude Code and fixed with a targeted unit test before it could affect production data.

**Verifiable evidence:** See [coding-agent-evidence/](coding-agent-evidence/) — contains the exact prompts used, Claude Code session exports, screenshots, and a session-by-session changelog. Raw session JSON files are excluded from the repo; only curated `session.md` summaries are committed.

This coding-agent contribution qualifies for the **hackathon +2 bonus** per AgentHack 2026 rules.

---

## Setup instructions

### Prerequisites

- Python 3.11 or 3.12
- An OpenAI API key (for LLM-assisted rules R002–R007)

### Install

```bash
# 1. Clone the repo
git clone https://github.com/<your-org>/disputeops.git
cd disputeops

# 2. Create and activate a virtual environment
python -m venv .venv

# Mac / Linux
source .venv/bin/activate

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# 3. Install in editable mode (includes all agent packages)
pip install -e .

# 4. Configure environment
cp .env.example .env
# Edit .env and set OPENAI_API_KEY and optionally COMPLIANCE_AUDITOR_MODEL
```

### Run the tests

```bash
pytest
# Expected: 255 passed
```

For a coverage report:

```bash
pytest --cov=agents --cov-report=term-missing
```

### Run a sample audit

```bash
# Coming in Week 2 when the case simulator is wired up
python -m orchestration.simulator --fixture clean_violation_case
```

### Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes | — | OpenAI API key for LLM-assisted rules |
| `COMPLIANCE_AUDITOR_MODEL` | No | `gpt-4o-mini` | Model used for R003–R007 |
| `LOG_LEVEL` | No | `INFO` | Python logging level |

---

## Repository structure

```
disputeops/
├── agents/                              ✅ Built
│   ├── compliance_auditor/              ✅ Built — LangGraph FMC compliance engine
│   │   ├── compliance_auditor.py        ✅ LangGraph agent graph
│   │   ├── rules.py                     ✅ 12 FMC Part 541 rules
│   │   ├── citations.py                 ✅ CFR citation engine
│   │   ├── prompts.py                   ✅ LangChain structured prompts
│   │   └── __init__.py
│   ├── rules_engine/                    ✅ Built
│   ├── shared/                          ✅ Built — LLM client, exceptions
│   │   ├── llm_client.py
│   │   ├── exceptions.py
│   │   └── __init__.py
│   └── __init__.py
├── data/
│   ├── fixtures/                        ✅ Built — test case fixtures
│   │   ├── clean_violation_case.py
│   │   ├── valid_charge_case.py
│   │   ├── borderline_case.py
│   │   └── expected_verdicts.py
│   └── schemas/                         ✅ Built — Pydantic v2 schemas
│       ├── case.py                      ✅ DetentionCase input model
│       └── verdict.py                   ✅ ComplianceVerdict output model
├── tests/
│   ├── unit/                            ✅ 205 tests
│   │   ├── test_citations.py            ✅ 78 citation tests
│   │   ├── test_rules.py                ✅ 104 rule tests
│   │   ├── test_compliance_auditor.py   ✅ 36 orchestration tests
│   │   ├── test_llm_client.py           ✅ 5 LLM client tests
│   │   └── conftest.py
│   └── integration/                     ✅ 32 tests
│       └── test_compliance_auditor.py   ✅ 22 integration + 10 cross-cutting
├── orchestration/                       ⏳ Planned — Maestro Case integration
├── docs/                                ⏳ Planned — architecture diagrams
├── coding-agent-evidence/               ✅ Initialized (curated session logs)
│   └── README.md
├── README.md                            ✅
├── LICENSE                              ✅ MIT
├── .gitignore                           ✅
├── pyproject.toml                       ✅
└── .env.example                         ✅
```

---

## Hackathon submission status

| Required submission item | Status |
|--------------------------|--------|
| Public GitHub repo with MIT license | ✅ |
| README with all four required sections | ✅ |
| Working project running on UiPath Automation Cloud | ⏳ Pending Labs access |
| Demo video (≤ 5 minutes on YouTube/Vimeo) | ⏳ Planned |
| Presentation deck (UiPath template) | ⏳ Planned |
| Use Case post on UiPath Community Forum (if finalist) | ⏳ If selected |

**Submission deadline: June 29, 2026**

---

## License

MIT — see [LICENSE](LICENSE).

---

## Acknowledgments

- [UiPath](https://www.uipath.com) for hosting AgentHack 2026 and providing the Maestro Case platform
- [Anthropic](https://www.anthropic.com) for Claude Code, which was used to build ComplianceAuditor
- The Federal Maritime Commission for publishing 46 CFR Part 541 in machine-readable form
