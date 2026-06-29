\# DisputeOps — Maestro Case Solution Design Document



\## Project

\- \*\*Project name:\*\* DisputeOpe\_Test02

\- \*\*Important:\*\* Create as a NEW project. Do NOT modify the existing DisputeOpe\_Test01.

\- \*\*Case ID:\*\* Constant prefix "DEMO" (case IDs appear as DEMO-#####)

\- \*\*Tenant:\*\* staging.uipath.com, org hackathon26\_298, DefaultTenant



\## Business Context

DisputeOps is an agentic case-management solution for US freight \*\*detention \& demurrage (D\&D) dispute recovery\*\*, shipper-side. US shippers lose an FMC-documented \~$15.4B/year to improper D\&D charges. The system is anchored in FMC regulation \*\*46 CFR Part 541\*\* (the 2024 billing-requirements rewrite). Each case represents one disputed freight invoice moving from intake, through evidence gathering and agentic analysis, to a filed dispute and a resolution outcome. Agents handle routine cases autonomously and escalate only uncertain or high-value cases to a human.



\## Case Lifecycle (8 stages)

Primary (happy path, sequential): \*\*Intake → Evidence → Analysis → Filing\*\*

Secondary / conditional: \*\*Approval\*\* (entered only when human review is required)

Terminal (case outcomes): \*\*Credit Issued\*\*, \*\*Written Off\*\*, \*\*Escalated to FMC\*\*



\## Case Data (12 variables, all root scope)

| Variable | Type | Purpose |

|---|---|---|

| case\_id | String | Case identifier |

| recommended\_action | String | Drives 3-way Analysis branch: auto\_file / human\_review / write\_off |

| carrier\_response | String | Drives Filing branch: credit / denied / silent |

| overall\_strength | String | ComplianceAuditor verdict strength |

| recovery\_probability | Float | RecoveryOracle probability of recovery |

| violation\_count | Integer | Number of FMC violations found |

| total\_amount\_usd | Float | Disputed charge amount |

| invoice\_data | JSON | Full invoice (from InvoiceLens) |

| evidence\_data | JSON | Full evidence package |

| verdict\_data | JSON | Full ComplianceVerdict |

| score\_data | JSON | Full RecoveryScore |

| letter\_data | JSON | Full demand letter |



\## Stages and Tasks



\### 1. Intake (primary)

Purpose: read and classify the incoming freight invoice.

\- \*\*InvoiceLens\*\* — UiPath IXP generative extraction; pulls 13 structured fields from the invoice PDF.

\- \*\*ChargeTagger\*\* — UiPath Agent Builder agent; classifies charge\_type (demurrage / detention / tonu / layover / lumper / other).

\- Writes: invoice\_data.



\### 2. Evidence (primary)

Purpose: gather supporting proof for the dispute.

\- \*\*GateHarvester\*\* — RPA workflow; scrapes gate-in/gate-out timestamps from the TMS.

\- \*\*CarrierBridge\*\* — API workflow; pulls carrier portal data.

\- Writes: evidence\_data.

\- Note: these RPA/API tasks are designed; represent as placeholder tasks if real implementations are unavailable.



\### 3. Analysis (primary)

Purpose: core agentic reasoning. Three coded Python agents run in sequence.

\- \*\*ComplianceAuditor\*\* — coded agent (LangGraph); evaluates 12 FMC Part 541 rules against invoice + evidence; returns ComplianceVerdict (violations, cannot\_evaluate, clean\_results, overall\_strength). Writes verdict\_data, overall\_strength, violation\_count. Published package: compliance-auditor-agent-v2.

\- \*\*RecoveryOracle\*\* — coded agent (Pydantic AI); scores recovery probability from the verdict; outputs recommended\_action (auto\_file / human\_review / write\_off). Writes recovery\_probability, recommended\_action, score\_data. Published package: recovery-oracle-agent-v2.

\- \*\*DemandSmith\*\* — coded agent (LangChain); drafts the FMC demand letter citing violations. Writes letter\_data. Published package: demand-smith-agent-v2.

\- Stage exits based on recommended\_action.



\### 4. Approval (secondary / conditional)

Purpose: human-in-the-loop review at the key decision point.

\- \*\*ApprovalGate\*\* — UiPath Action Center human task; reviewer approves or rejects the dispute.

\- Entered only when recommended\_action == "human\_review".

\- On approve → Filing; on reject → Written Off.



\### 5. Filing (primary)

Purpose: submit and track the dispute.

\- \*\*FilingRunner\*\* — RPA workflow; submits the dispute to the carrier/FMC.

\- Exits based on carrier\_response.



\### Terminal stages

\- \*\*Credit Issued\*\* — carrier paid the dispute (success).

\- \*\*Written Off\*\* — no merit, or human rejected (closed).

\- \*\*Escalated to FMC\*\* — carrier denied or went silent (escalation).



\## Transition Conditions (exit-stage-if, reading case data)

| From | To | Condition |

|---|---|---|

| Analysis | Filing | recommended\_action == "auto\_file" |

| Analysis | Approval | recommended\_action == "human\_review" |

| Analysis | Written Off | recommended\_action == "write\_off" |

| Approval | Filing | human approved |

| Approval | Written Off | human rejected |

| Filing | Credit Issued | carrier\_response indicates payment |

| Filing | Escalated to FMC | carrier\_response indicates denial/silence |



\## Agent Types

This solution uses \*\*both\*\* coded agents (ComplianceAuditor, RecoveryOracle, DemandSmith — Python, external frameworks) and low-code / UiPath-native agents (ChargeTagger via Agent Builder; InvoiceLens via IXP). Coded agents were built and deployed using UiPath for Coding Agents (Claude Code).

