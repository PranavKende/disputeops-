Schema: v20

<!-- DisputeOps — Maestro Case plan. Generated from sdd.md. Trigger: Manual. All 12 case-data fields are internal Variables. -->

## Inventory

| Class | Count | T-entries |
|---|---|---|
| Case file | 1 | T01 |
| Triggers (manual) | 1 | T02 |
| Variables | 12 | T03–T14 |
| Stages | 8 | T15–T22 |
| Edges | 10 | T23–T32 |
| Tasks | 9 (2 resolved, 7 placeholder) | T33–T41 |
| Stage-exit conditions | 12 | T42–T53 |
| Case-exit conditions | 3 | T54–T56 |
| Task-entry conditions | 3 | T57–T59 |
| SLA | 0 | — |
| **Total T-entries** | **59** | T01–T59 |

Resolved tasks: ComplianceAuditor → `compliance-auditor-agent-v2`, RecoveryOracle → `recovery-oracle-agent-v2`.
Placeholders (7): InvoiceLens, ChargeTagger, GateHarvester, CarrierBridge, DemandSmith, ApprovalGate, FilingRunner.

## T01: Create case file "DisputeOps"
- file: "DisputeOps/DisputeOpe_Test02/caseplan.json"
- case-identifier: "DEMO"
- identifier-type: constant
- case-app-enabled: true
- description: "Agentic D&D dispute recovery — one disputed freight invoice from intake through agentic analysis to a filed dispute and resolution outcome (FMC 46 CFR Part 541)."
- order: first
- verify: Confirm caseplan.json written and parses; root.id == "root", nodes == [], edges == []

## T02: Configure manual trigger "Start Manually"
- display-name: "Start Manually"
- description: "Operator starts a new dispute case (one disputed freight invoice) from the Case App."
- order: after T01
- verify: Confirm node appended to caseplan.json.nodes and matching entry appended to entry-points.json.entryPoints; capture TriggerId

## T03: Declare Variable "case_id"
- category: Variable
- type: string
- verify: inputOutputs[] entry (id=case_id, elementId="root"); no trigger output entries.

## T04: Declare Variable "recommended_action"
- category: Variable
- type: string
- verify: inputOutputs[] entry (id=recommended_action, elementId="root"). Drives 3-way Analysis branch: auto_file / human_review / write_off. Written by RecoveryOracle (T38).

## T05: Declare Variable "carrier_response"
- category: Variable
- type: string
- verify: inputOutputs[] entry (id=carrier_response, elementId="root"). Drives Filing branch: credit / denied / silent. Written by FilingRunner (T41, placeholder).

## T06: Declare Variable "overall_strength"
- category: Variable
- type: string
- verify: inputOutputs[] entry (id=overall_strength, elementId="root"). ComplianceAuditor verdict strength. Written by ComplianceAuditor (T37).

## T07: Declare Variable "recovery_probability"
- category: Variable
- type: float
- verify: inputOutputs[] entry (id=recovery_probability, elementId="root"). RecoveryOracle probability of recovery. Written by RecoveryOracle (T38).

## T08: Declare Variable "violation_count"
- category: Variable
- type: integer
- verify: inputOutputs[] entry (id=violation_count, elementId="root"). Number of FMC violations found. Written by ComplianceAuditor (T37).

## T09: Declare Variable "total_amount_usd"
- category: Variable
- type: float
- verify: inputOutputs[] entry (id=total_amount_usd, elementId="root"). Disputed charge amount.

## T10: Declare Variable "invoice_data"
- category: Variable
- type: jsonSchema
- verify: inputOutputs[] entry (id=invoice_data, elementId="root"). Full invoice (from InvoiceLens, T33).

## T11: Declare Variable "evidence_data"
- category: Variable
- type: jsonSchema
- verify: inputOutputs[] entry (id=evidence_data, elementId="root"). Full evidence package (Evidence stage tasks).

## T12: Declare Variable "verdict_data"
- category: Variable
- type: jsonSchema
- verify: inputOutputs[] entry (id=verdict_data, elementId="root"). Full ComplianceVerdict (from ComplianceAuditor, T37).

## T13: Declare Variable "score_data"
- category: Variable
- type: jsonSchema
- verify: inputOutputs[] entry (id=score_data, elementId="root"). Full RecoveryScore (from RecoveryOracle, T38).

## T14: Declare Variable "letter_data"
- category: Variable
- type: jsonSchema
- verify: inputOutputs[] entry (id=letter_data, elementId="root"). Full demand letter (from DemandSmith, T39, placeholder).

## T15: Create stage "Intake"
- type: stage
- description: "Read and classify the incoming freight invoice."
- isRequired: true
- order: after T14
- verify: Confirm Result: Success, capture StageId

## T16: Create stage "Evidence"
- type: stage
- description: "Gather supporting proof for the dispute."
- isRequired: true
- order: after T15
- verify: Confirm Result: Success, capture StageId

## T17: Create stage "Analysis"
- type: stage
- description: "Core agentic reasoning — three coded agents run in sequence; stage exits on recommended_action."
- isRequired: true
- order: after T16
- verify: Confirm Result: Success, capture StageId

## T18: Create stage "Approval"
- type: stage
- description: "Human-in-the-loop review at the key decision point. Entered only when recommended_action == human_review."
- isRequired: false
- order: after T17
- verify: Confirm Result: Success, capture StageId

## T19: Create stage "Filing"
- type: stage
- description: "Submit and track the dispute; exits on carrier_response."
- isRequired: false
- order: after T18
- verify: Confirm Result: Success, capture StageId

## T20: Create stage "Credit Issued"
- type: stage
- description: "Terminal outcome — carrier paid the dispute (success)."
- isRequired: false
- order: after T19
- verify: Confirm Result: Success, capture StageId

## T21: Create stage "Written Off"
- type: stage
- description: "Terminal outcome — no merit, or human rejected (closed)."
- isRequired: false
- order: after T20
- verify: Confirm Result: Success, capture StageId

## T22: Create stage "Escalated to FMC"
- type: stage
- description: "Terminal outcome — carrier denied or went silent (escalation)."
- isRequired: false
- order: after T21
- verify: Confirm Result: Success, capture StageId

## T23: Add edge "Start Manually" → "Intake"
- source: "Start Manually"
- target: "Intake"
- label: "Start"
- order: after T22
- verify: Confirm Result: Success, capture EdgeId (TriggerEdge)

## T24: Add edge "Intake" → "Evidence"
- source: "Intake"
- target: "Evidence"
- order: after T23
- verify: Confirm Result: Success, capture EdgeId

## T25: Add edge "Evidence" → "Analysis"
- source: "Evidence"
- target: "Analysis"
- order: after T24
- verify: Confirm Result: Success, capture EdgeId

## T26: Add edge "Analysis" → "Filing"
- source: "Analysis"
- target: "Filing"
- label: "Auto-file"
- order: after T25
- verify: Confirm Result: Success, capture EdgeId

## T27: Add edge "Analysis" → "Approval"
- source: "Analysis"
- target: "Approval"
- label: "Human review"
- order: after T26
- verify: Confirm Result: Success, capture EdgeId

## T28: Add edge "Analysis" → "Written Off"
- source: "Analysis"
- target: "Written Off"
- label: "Write off"
- order: after T27
- verify: Confirm Result: Success, capture EdgeId

## T29: Add edge "Approval" → "Filing"
- source: "Approval"
- target: "Filing"
- label: "Approved"
- order: after T28
- verify: Confirm Result: Success, capture EdgeId

## T30: Add edge "Approval" → "Written Off"
- source: "Approval"
- target: "Written Off"
- label: "Rejected"
- order: after T29
- verify: Confirm Result: Success, capture EdgeId

## T31: Add edge "Filing" → "Credit Issued"
- source: "Filing"
- target: "Credit Issued"
- label: "Credit"
- order: after T30
- verify: Confirm Result: Success, capture EdgeId

## T32: Add edge "Filing" → "Escalated to FMC"
- source: "Filing"
- target: "Escalated to FMC"
- label: "Denied / silent"
- order: after T31
- verify: Confirm Result: Success, capture EdgeId

## T33: Add process task "InvoiceLens" to "Intake"
- taskTypeId: <UNRESOLVED: UiPath IXP generative extraction model not in case registry / no published process on this tenant>
- folder-path: <UNRESOLVED>
- runOnlyOnce: true
- isRequired: true
- order: after T32
- lane: 0
- verify: Confirm Result: Success, capture TaskId (placeholder — user to attach the IXP extraction step + bindings)
```text
wiring notes (user must attach after publishing the IXP extraction):
  input:  invoice PDF supplied at case start
  outputs -> invoice_data (full invoice; 13 structured fields)
  outputs -> total_amount_usd (disputed charge amount)
```

## T34: Add agent task "ChargeTagger" to "Intake"
- taskTypeId: <UNRESOLVED: Agent Builder agent "ChargeTagger" not found in agent-index.json on this tenant>
- folder-path: <UNRESOLVED>
- runOnlyOnce: true
- isRequired: true
- order: after T33
- lane: 1
- verify: Confirm Result: Success, capture TaskId (placeholder — user to attach the Agent Builder agent + bindings)
```text
wiring notes (user must attach after publishing the ChargeTagger agent):
  input:  invoice_data  (from "Intake"."InvoiceLens")
  outputs: charge_type (demurrage / detention / tonu / layover / lumper / other)
```

## T35: Add rpa task "GateHarvester" to "Evidence"
- taskTypeId: <UNRESOLVED: RPA workflow not published / process-index.json empty on this tenant>
- folder-path: <UNRESOLVED>
- runOnlyOnce: true
- isRequired: true
- order: after T34
- lane: 0
- verify: Confirm Result: Success, capture TaskId (placeholder — user to attach the RPA workflow + bindings)
```text
wiring notes (user must attach after publishing the GateHarvester RPA workflow):
  input:  invoice_data  (TMS lookup keys)
  outputs -> evidence_data (gate-in / gate-out timestamps scraped from the TMS)
```

## T36: Add api-workflow task "CarrierBridge" to "Evidence"
- taskTypeId: <UNRESOLVED: API workflow not published / api-index.json empty on this tenant>
- folder-path: <UNRESOLVED>
- runOnlyOnce: true
- isRequired: true
- order: after T35
- lane: 1
- verify: Confirm Result: Success, capture TaskId (placeholder — user to attach the API workflow + bindings)
```text
wiring notes (user must attach after publishing the CarrierBridge API workflow):
  input:  invoice_data  (carrier portal lookup keys)
  outputs -> evidence_data (carrier portal data, merged into the evidence package)
```

## T37: Add agent task "ComplianceAuditor" to "Analysis"
- taskTypeId: a2e8020e-643e-4309-871b-daaa737a7a45
- folder-path: "Shared"
- name: "compliance-auditor-agent-v2"
- inputs:
  - invoice_data <- "Intake"."InvoiceLens".invoice_data
  - evidence_data <- "Evidence"."GateHarvester".evidence_data
- outputs: verdict_data, overall_strength, violation_count   # confirm exact names via `tasks describe` in Phase 3
- runOnlyOnce: true
- isRequired: true
- order: after T36
- lane: 0
- verify: Confirm Result: Success, capture TaskId; outputs wired to verdict_data / overall_strength / violation_count

## T38: Add agent task "RecoveryOracle" to "Analysis"
- taskTypeId: d61b02b3-a02b-448b-91e0-e4a528f3ff1c
- folder-path: "Shared"
- name: "recovery-oracle-agent-v2"
- inputs:
  - verdict_data <- "Analysis"."ComplianceAuditor".verdict_data
- outputs: recovery_probability, recommended_action, score_data   # confirm exact names via `tasks describe` in Phase 3
- runOnlyOnce: true
- isRequired: true
- order: after T37
- lane: 1
- verify: Confirm Result: Success, capture TaskId; outputs wired to recovery_probability / recommended_action / score_data

## T39: Add agent task "DemandSmith" to "Analysis"
- taskTypeId: <UNRESOLVED: coded agent "demand-smith-agent-v2" not found in agent-index.json on this tenant>
- folder-path: <UNRESOLVED>
- runOnlyOnce: true
- isRequired: true
- order: after T38
- lane: 2
- verify: Confirm Result: Success, capture TaskId (placeholder — user to attach demand-smith-agent-v2 + bindings)
```text
wiring notes (user must attach after publishing demand-smith-agent-v2):
  input:  verdict_data  (from "Analysis"."ComplianceAuditor")
  outputs -> letter_data (FMC demand letter citing violations)
```

## T40: Add action task "ApprovalGate" to "Approval"
- taskTypeId: <UNRESOLVED: Action Center action app not found in action-apps-index.json on this tenant>
- taskTitle: "Review D&D dispute"
- runOnlyOnce: true
- isRequired: true
- order: after T39
- lane: 0
- verify: Confirm Result: Success, capture TaskId (placeholder — user to attach the Action Center app + bindings)
```text
wiring notes (user must attach after creating the ApprovalGate action app):
  inputs:  verdict_data, score_data, letter_data, total_amount_usd, recovery_probability
  outcome: approve -> route to Filing ; reject -> route to Written Off
  NOTE: the approve/reject branch expressions on the Approval stage-exit conditions (T47, T48)
        must be bound to this action's decision output once the app is attached.
```

## T41: Add rpa task "FilingRunner" to "Filing"
- taskTypeId: <UNRESOLVED: RPA workflow not published / process-index.json empty on this tenant>
- folder-path: <UNRESOLVED>
- runOnlyOnce: true
- isRequired: true
- order: after T40
- lane: 0
- verify: Confirm Result: Success, capture TaskId (placeholder — user to attach the RPA workflow + bindings)
```text
wiring notes (user must attach after publishing the FilingRunner RPA workflow):
  input:  letter_data, evidence_data  (dispute submission package)
  outputs -> carrier_response (credit / denied / silent)
```

## T42: Add stage-exit condition for "Intake" — complete when tasks done
- target-stage: "Intake"
- display-name: "Intake complete"
- type: exit-only
- marks-stage-complete: true
- rule-type: required-tasks-completed
- order: after T41
- verify: Confirm Result: Success, capture ConditionId

## T43: Add stage-exit condition for "Evidence" — complete when tasks done
- target-stage: "Evidence"
- display-name: "Evidence complete"
- type: exit-only
- marks-stage-complete: true
- rule-type: required-tasks-completed
- order: after T42
- verify: Confirm Result: Success, capture ConditionId

## T44: Add stage-exit condition for "Analysis" — route to Filing (auto_file)
- target-stage: "Analysis"
- display-name: "Auto-file"
- type: exit-only
- exit-to-stage: "Filing"
- marks-stage-complete: false
- rule-type: selected-tasks-completed
- selected-tasks: "ComplianceAuditor, RecoveryOracle, DemandSmith"
- condition-expression: "=js:vars.recommended_action === 'auto_file'"
- order: after T43
- verify: Confirm Result: Success, capture ConditionId

## T45: Add stage-exit condition for "Analysis" — route to Approval (human_review)
- target-stage: "Analysis"
- display-name: "Human review"
- type: exit-only
- exit-to-stage: "Approval"
- marks-stage-complete: false
- rule-type: selected-tasks-completed
- selected-tasks: "ComplianceAuditor, RecoveryOracle, DemandSmith"
- condition-expression: "=js:vars.recommended_action === 'human_review'"
- order: after T44
- verify: Confirm Result: Success, capture ConditionId

## T46: Add stage-exit condition for "Analysis" — route to Written Off (write_off)
- target-stage: "Analysis"
- display-name: "Write off"
- type: exit-only
- exit-to-stage: "Written Off"
- marks-stage-complete: false
- rule-type: selected-tasks-completed
- selected-tasks: "ComplianceAuditor, RecoveryOracle, DemandSmith"
- condition-expression: "=js:vars.recommended_action === 'write_off'"
- order: after T45
- verify: Confirm Result: Success, capture ConditionId

## T47: Add stage-exit condition for "Approval" — route to Filing (approved)
- target-stage: "Approval"
- display-name: "Approved"
- type: exit-only
- exit-to-stage: "Filing"
- marks-stage-complete: false
- rule-type: selected-tasks-completed
- selected-tasks: "ApprovalGate"
- order: after T46
- verify: Confirm Result: Success, capture ConditionId
```text
gating note (placeholder): bind condition-expression to the ApprovalGate decision output
  (e.g. =js:vars.<approvalDecision> === 'approved') once the Action Center app is attached.
```

## T48: Add stage-exit condition for "Approval" — route to Written Off (rejected)
- target-stage: "Approval"
- display-name: "Rejected"
- type: exit-only
- exit-to-stage: "Written Off"
- marks-stage-complete: false
- rule-type: selected-tasks-completed
- selected-tasks: "ApprovalGate"
- order: after T47
- verify: Confirm Result: Success, capture ConditionId
```text
gating note (placeholder): bind condition-expression to the ApprovalGate decision output
  (e.g. =js:vars.<approvalDecision> === 'rejected') once the Action Center app is attached.
```

## T49: Add stage-exit condition for "Filing" — route to Credit Issued (paid)
- target-stage: "Filing"
- display-name: "Credit"
- type: exit-only
- exit-to-stage: "Credit Issued"
- marks-stage-complete: false
- rule-type: selected-tasks-completed
- selected-tasks: "FilingRunner"
- condition-expression: "=js:vars.carrier_response === 'credit'"
- order: after T48
- verify: Confirm Result: Success, capture ConditionId

## T50: Add stage-exit condition for "Filing" — route to Escalated to FMC (denied/silent)
- target-stage: "Filing"
- display-name: "Denied / silent"
- type: exit-only
- exit-to-stage: "Escalated to FMC"
- marks-stage-complete: false
- rule-type: selected-tasks-completed
- selected-tasks: "FilingRunner"
- condition-expression: "=js:vars.carrier_response === 'denied' || vars.carrier_response === 'silent'"
- order: after T49
- verify: Confirm Result: Success, capture ConditionId

## T51: Add stage-exit condition for "Credit Issued" — terminal complete
- target-stage: "Credit Issued"
- display-name: "Credit Issued reached"
- type: exit-only
- marks-stage-complete: true
- rule-type: required-tasks-completed
- order: after T50
- verify: Confirm Result: Success, capture ConditionId (no tasks → completes on entry)

## T52: Add stage-exit condition for "Written Off" — terminal complete
- target-stage: "Written Off"
- display-name: "Written Off reached"
- type: exit-only
- marks-stage-complete: true
- rule-type: required-tasks-completed
- order: after T51
- verify: Confirm Result: Success, capture ConditionId (no tasks → completes on entry)

## T53: Add stage-exit condition for "Escalated to FMC" — terminal complete
- target-stage: "Escalated to FMC"
- display-name: "Escalated to FMC reached"
- type: exit-only
- marks-stage-complete: true
- rule-type: required-tasks-completed
- order: after T52
- verify: Confirm Result: Success, capture ConditionId (no tasks → completes on entry)

## T54: Add case-exit condition — Credit Issued ends the case
- display-name: "Resolved — Credit Issued"
- marks-case-complete: false
- rule-type: selected-stage-completed
- selected-stage: "Credit Issued"
- order: after T53
- verify: Confirm Result: Success, capture ConditionId

## T55: Add case-exit condition — Written Off ends the case
- display-name: "Closed — Written Off"
- marks-case-complete: false
- rule-type: selected-stage-completed
- selected-stage: "Written Off"
- order: after T54
- verify: Confirm Result: Success, capture ConditionId

## T56: Add case-exit condition — Escalated to FMC ends the case
- display-name: "Closed — Escalated to FMC"
- marks-case-complete: false
- rule-type: selected-stage-completed
- selected-stage: "Escalated to FMC"
- order: after T55
- verify: Confirm Result: Success, capture ConditionId

## T57: Add task-entry condition for "ChargeTagger" — after InvoiceLens
- target-task: "ChargeTagger"
- target-stage: "Intake"
- display-name: "After InvoiceLens"
- rule-type: selected-tasks-completed
- selected-tasks: "InvoiceLens"
- order: after T56
- verify: Confirm Result: Success, capture ConditionId

## T58: Add task-entry condition for "RecoveryOracle" — after ComplianceAuditor
- target-task: "RecoveryOracle"
- target-stage: "Analysis"
- display-name: "After ComplianceAuditor"
- rule-type: selected-tasks-completed
- selected-tasks: "ComplianceAuditor"
- order: after T57
- verify: Confirm Result: Success, capture ConditionId

## T59: Add task-entry condition for "DemandSmith" — after RecoveryOracle
- target-task: "DemandSmith"
- target-stage: "Analysis"
- display-name: "After RecoveryOracle"
- rule-type: selected-tasks-completed
- selected-tasks: "RecoveryOracle"
- order: after T58
- verify: Confirm Result: Success, capture ConditionId

## Not Covered (notes for the user — outside caseplan.json scope)

- **SLA / escalation:** the sdd.md defines no case-level, stage-level, or task-level SLA. None emitted. Add later if needed.
- **IXP extraction model:** InvoiceLens (the 13-field generative extraction) is a UiPath IXP artifact, not a case-registry task type. Modeled as a `process` placeholder; attach the real IXP step (or wrap it in a process/agent) before runtime.
- **FMC Part 541 rules engine:** the 12-rule logic lives inside compliance-auditor-agent-v2; not modeled in the case plan.
- **Data Fabric / persistence:** no entity schemas are defined in the sdd.md; case-data fields are modeled as in-case Variables only.
- **Approval decision variable:** the approve/reject outcome is not one of the 12 declared variables; the Approval branch (T47/T48) gating expression must be bound to the ApprovalGate action output after attaching the action app.
