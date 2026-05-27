"""LangGraph orchestration for ComplianceAuditor — DisputeOps Stage 3 agent.

This module wires the 12 rule functions from ``rules.py`` into a compiled
LangGraph ``StateGraph`` with:

  - Dependency-ordered stages (critical gate before parallel rules)
  - True parallel fan-out to 10 rules once the gate passes
  - Short-circuit on critical R002 failure (missing BOL/container identifiers)
  - Per-rule error isolation (a crashing rule produces a cannot_evaluate result,
    never an aborted audit)
  - Final verdict synthesis aggregating all RuleResults into a ComplianceVerdict

Graph topology
--------------
::

    START
      └─► [gate]          R001 + R002; sets short_circuit if identifiers absent
            │
            ├─ short_circuit=True ─────────────────────► [synthesize] ──► END
            │
            └─ short_circuit=False
                  │
                  ├─► [run_r003]  ─┐
                  ├─► [run_r004]  ─┤
                  ├─► [run_r005]  ─┤
                  ├─► [run_r006]  ─┤  parallel fan-out (results merged via reducer)
                  ├─► [run_r007]  ─┤
                  ├─► [run_r008]  ─┤
                  ├─► [run_r009]  ─┤
                  ├─► [run_r010]  ─┤
                  ├─► [run_r011]  ─┤
                  └─► [run_r012]  ─┘
                            │
                            └─────────────────────────► [synthesize] ──► END

Short-circuit condition
-----------------------
R002 violated=True with a ``missing_evidence`` item pointing to
``invoice.bol_number`` or ``invoice.container_number``.  Without primary invoice
identifiers, evidence-matching rules (R003, R004, R005) would audit an
unidentifiable charge.  The short-circuit still produces a full ComplianceVerdict
from R001 + R002 alone with overall_strength="strong" where warranted.

Error isolation
---------------
Every rule node wraps its call in ``_safe_evaluate()``.  If a rule function
raises, a cannot_evaluate RuleResult is injected into the results dict and the
audit continues.  One broken rule never aborts the whole run.

Public interface
----------------
Use ``run_audit(case)`` for the convenience wrapper.  The compiled graph is also
exposed as ``audit_graph`` for testing and LangGraph Studio inspection.

    >>> from agents.compliance_auditor.compliance_auditor import run_audit
    >>> verdict = run_audit(case)
    >>> verdict.overall_strength
    'strong'
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from agents.compliance_auditor.rules import (
    evaluate_R001,
    evaluate_R002,
    evaluate_R003,
    evaluate_R004,
    evaluate_R005,
    evaluate_R006,
    evaluate_R007,
    evaluate_R008,
    evaluate_R009,
    evaluate_R010,
    evaluate_R011,
    evaluate_R012,
)
from agents.compliance_auditor.citations import get_citation
from data.schemas.case import DisputeCase
from data.schemas.verdict import (
    CannotEvaluate,
    ComplianceVerdict,
    MissingEvidenceItem,
    RuleResult,
    Violation,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Fields whose absence triggers the short-circuit path.
# Without a BOL number or container number the charge cannot be identified,
# making evidence-matching rules unreliable.
_CRITICAL_MISSING_FIELDS: frozenset[str] = frozenset({
    "invoice.bol_number",
    "invoice.container_number",
})

# All 12 rule IDs in evaluation order (used for result ordering in the verdict).
_ALL_RULE_IDS: list[str] = [
    "R001", "R002", "R003", "R004", "R005", "R006",
    "R007", "R008", "R009", "R010", "R011", "R012",
]

# Rule nodes that run in parallel after the gate passes.
_PARALLEL_RULE_IDS: list[str] = [
    "R003", "R004", "R005", "R006", "R007",
    "R008", "R009", "R010", "R011", "R012",
]


# ---------------------------------------------------------------------------
# LangGraph state
# ---------------------------------------------------------------------------

def _merge_results(
    existing: dict[str, RuleResult],
    incoming: dict[str, RuleResult],
) -> dict[str, RuleResult]:
    """Reducer: merge two RuleResult dicts, incoming wins on collision."""
    return {**existing, **incoming}


class AuditState(TypedDict):
    """Mutable state flowing through the ComplianceAuditor graph."""

    case: DisputeCase
    """Input case — never modified by any node."""

    results: Annotated[dict[str, RuleResult], _merge_results]
    """Accumulated per-rule results.  Each node writes exactly one entry."""

    short_circuit: bool
    """Set by gate if R002 detects critical missing identifiers.
    When True the parallel rule nodes are skipped and synthesize runs immediately."""

    verdict: ComplianceVerdict | None
    """Populated by synthesize; None until that node runs."""


# ---------------------------------------------------------------------------
# Error isolation helper
# ---------------------------------------------------------------------------

def _safe_evaluate(
    rule_fn,
    rule_id: str,
    case: DisputeCase,
) -> RuleResult:
    """Call ``rule_fn(case)`` and catch any exception.

    On success returns the RuleResult.  On failure returns a cannot_evaluate
    RuleResult so the audit can continue without the broken rule's result.
    """
    try:
        return rule_fn(case)
    except Exception as exc:                                    # noqa: BLE001
        logger.error(
            "Rule %s raised an unexpected error — injecting cannot_evaluate: %r",
            rule_id, exc,
        )
        try:
            citation_text = get_citation(rule_id).verbatim_text
        except Exception:                                       # noqa: BLE001
            citation_text = f"[citation unavailable for {rule_id}]"

        return RuleResult(
            rule_id=rule_id,
            rule_name="[evaluation error]",
            violated=None,
            confidence=0.0,
            evidence_refs=[],
            reasoning=f"Rule evaluation raised {type(exc).__name__}: {exc!r}. "
                      "This rule's result has been excluded from the verdict.",
            citation=citation_text,
            missing_evidence=[
                MissingEvidenceItem(
                    field_path="[evaluation_error]",
                    description=(
                        f"Rule {rule_id} raised an unexpected exception during evaluation: "
                        f"{type(exc).__name__}"
                    ),
                    can_be_fetched_from=None,
                )
            ],
        )


# ---------------------------------------------------------------------------
# Module-level rule evaluator registry
# ---------------------------------------------------------------------------
# Nodes look up functions here at call time (not at closure-capture time).
# This late-binding design makes individual evaluators patchable in tests via
# ``mocker.patch.dict(ca._RULE_EVALUATORS, {"R009": my_stub})``.
# ---------------------------------------------------------------------------

_RULE_EVALUATORS: dict[str, object] = {
    "R001": evaluate_R001,
    "R002": evaluate_R002,
    "R003": evaluate_R003,
    "R004": evaluate_R004,
    "R005": evaluate_R005,
    "R006": evaluate_R006,
    "R007": evaluate_R007,
    "R008": evaluate_R008,
    "R009": evaluate_R009,
    "R010": evaluate_R010,
    "R011": evaluate_R011,
    "R012": evaluate_R012,
}


# ---------------------------------------------------------------------------
# Short-circuit predicate
# ---------------------------------------------------------------------------

def _is_critical_r002_failure(result: RuleResult) -> bool:
    """True when R002 is violated due to missing primary invoice identifiers.

    Checks for ``invoice.bol_number`` or ``invoice.container_number`` in the
    missing_evidence list.  Without these, evidence-matching rules cannot
    reliably tie the invoice to terminal records or tariff references.
    """
    if result.violated is not True or not result.missing_evidence:
        return False
    return any(
        item.field_path in _CRITICAL_MISSING_FIELDS
        for item in result.missing_evidence
    )


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------

def _gate_node(state: AuditState) -> dict:
    """Run R001 and R002; set short_circuit if critical identifiers are absent.

    R001 (invoicing window) and R002 (required minimum content) are the two
    rules that gate everything else:

    - R001 failure means the invoice is untimely — other rules still run but
      the overall case is already strong.
    - R002 critical failure (BOL or container number absent) means subsequent
      rules cannot safely identify what they are auditing.  short_circuit=True
      skips the parallel stage.
    """
    case = state["case"]
    logger.info("gate: evaluating R001 + R002 for case %s", case.case_id)

    r001 = _safe_evaluate(_RULE_EVALUATORS["R001"], "R001", case)
    r002 = _safe_evaluate(_RULE_EVALUATORS["R002"], "R002", case)

    short_circuit = _is_critical_r002_failure(r002)
    if short_circuit:
        logger.warning(
            "gate: R002 critical failure for case %s — short-circuiting "
            "to synthesize (missing: %s)",
            case.case_id,
            [m.field_path for m in (r002.missing_evidence or [])],
        )

    return {
        "results": {"R001": r001, "R002": r002},
        "short_circuit": short_circuit,
    }


def _route_gate(state: AuditState) -> str | list[str]:
    """Conditional router after the gate node.

    Returns:
        ``"synthesize"`` when short_circuit is True (skip parallel rules).
        A list of rule-node names for the parallel fan-out otherwise.
    """
    if state["short_circuit"]:
        logger.info("gate router: short_circuit=True → synthesize directly")
        return "synthesize"
    logger.info("gate router: short_circuit=False → parallel rule fan-out")
    return [f"run_{rid.lower()}" for rid in _PARALLEL_RULE_IDS]


def _make_rule_node(rule_id: str):
    """Factory: return a LangGraph node function that runs one rule.

    The returned function looks up the evaluator in ``_RULE_EVALUATORS`` at
    call time (late binding), which makes individual evaluators patchable in
    tests without recompiling the graph.

    The returned function writes exactly one entry to ``state["results"]``
    under the rule's ID.
    """
    def node(state: AuditState) -> dict:
        logger.debug("%s node: evaluating for case %s", rule_id, state["case"].case_id)
        fn = _RULE_EVALUATORS[rule_id]
        result = _safe_evaluate(fn, rule_id, state["case"])
        return {"results": {rule_id: result}}
    node.__name__ = f"run_{rule_id.lower()}"
    return node


def _synthesize_node(state: AuditState) -> dict:
    """Aggregate all RuleResults into a ComplianceVerdict.

    Iterates over ``state["results"]`` in canonical rule order (R001–R012).
    Classifies each result:
      - violated=True   → Violation  (counts toward total_rules_evaluated)
      - violated=False  → clean rule ID (counts toward total_rules_evaluated)
      - violated=None   → CannotEvaluate (NOT counted in total_rules_evaluated)
    """
    case = state["case"]
    results = state["results"]
    logger.info("synthesize: building ComplianceVerdict for case %s", case.case_id)

    violations: list[Violation] = []
    clean_rule_ids: list[str] = []
    cannot_evals: list[CannotEvaluate] = []

    # Process in canonical order for deterministic output.
    for rule_id in _ALL_RULE_IDS:
        if rule_id not in results:
            # Rule was skipped (short-circuit) — omit from verdict entirely.
            logger.debug("synthesize: %s not in results (was short-circuited)", rule_id)
            continue

        r = results[rule_id]

        if r.violated is True:
            violations.append(Violation(
                rule_id=r.rule_id,
                rule_name=r.rule_name,
                confidence=r.confidence,
                evidence_refs=r.evidence_refs,
                reasoning=r.reasoning,
                citation=r.citation,
            ))

        elif r.violated is False:
            clean_rule_ids.append(r.rule_id)

        else:  # violated is None
            missing = r.missing_evidence or [
                MissingEvidenceItem(
                    field_path="[unknown]",
                    description="Rule could not be evaluated; no specific evidence gap recorded.",
                )
            ]
            cannot_evals.append(CannotEvaluate(
                rule_id=r.rule_id,
                rule_name=r.rule_name,
                missing_evidence=missing,
                reasoning=r.reasoning,
                citation=r.citation,
            ))

    strength = _compute_overall_strength(violations)
    summary = _build_summary(case, violations, cannot_evals, strength)

    verdict = ComplianceVerdict(
        case_id=case.case_id,
        total_rules_evaluated=len(violations) + len(clean_rule_ids),
        violations=violations,
        cannot_evaluate=cannot_evals,
        clean_results=clean_rule_ids,
        overall_strength=strength,
        summary=summary,
    )

    logger.info(
        "synthesize: verdict for case %s — strength=%s violations=%d "
        "clean=%d cannot_eval=%d",
        case.case_id, strength, len(violations), len(clean_rule_ids), len(cannot_evals),
    )
    return {"verdict": verdict}


# ---------------------------------------------------------------------------
# Verdict helper functions
# ---------------------------------------------------------------------------

def _compute_overall_strength(
    violations: list[Violation],
) -> Literal["strong", "moderate", "weak", "no_merit"]:
    """Map the violation list to an overall dispute-strength label.

    Thresholds (from ComplianceVerdict docstring):
      strong   ≥ 2 violations with confidence ≥ 0.8
      moderate 1 violation with confidence ≥ 0.8, OR ≥ 2 violations with conf ≥ 0.6
      weak     violations exist but all have confidence < 0.6
      no_merit no violations found
    """
    if not violations:
        return "no_merit"

    high  = sum(1 for v in violations if v.confidence >= 0.8)
    medium = sum(1 for v in violations if 0.6 <= v.confidence < 0.8)

    if high >= 2:
        return "strong"
    if high >= 1 or medium >= 2:
        return "moderate"
    if medium == 1:
        return "moderate"   # single medium-confidence violation still actionable
    return "weak"           # all violations below 0.6 — insufficient for a letter


def _build_summary(
    case: DisputeCase,
    violations: list[Violation],
    cannot_evals: list[CannotEvaluate],
    strength: Literal["strong", "moderate", "weak", "no_merit"],
) -> str:
    """Generate a demand-letter-ready 2–4 sentence summary.

    The opening line names the carrier, billed party, charge amount, charge
    type, and primary container or BOL identifier so the output is usable as
    a letter opener without further editing.

    Template variables are extracted from ``case.invoice``; all are None-safe:

    - ``carrier_name``    — always populated on a valid Invoice
    - ``billed_party``    — falls back to ``"the billed party"`` if None
    - ``charge_amount``   — formatted as ``$N,NNN.NN`` with comma separators
    - ``charge_type``     — e.g. ``"detention"`` or ``"demurrage"``
    - ``container_or_bol``— prefers ``container_number``, falls back to
                            ``bol_number``, then ``"an unspecified container"``

    Args:
        case: Full ``DisputeCase`` — invoice fields used for the opener.
        violations: Violation list from the synthesize node.
        cannot_evals: CannotEvaluate list from the synthesize node.
        strength: Overall strength label from ``_compute_overall_strength``.

    Returns:
        A 2–4 sentence demand-letter-ready plain-English summary.
    """
    inv = case.invoice

    # --- Opener variables — all None-safe ---
    carrier      = inv.carrier_name
    billed_party = inv.billed_to_party or "the billed party"
    charge_amt   = f"${inv.total_amount_usd:,.2f}"
    charge_type  = inv.charge_type
    id_label     = (
        inv.container_number
        or inv.bol_number
        or "an unspecified container"
    )

    # -----------------------------------------------------------------------
    # no_merit path
    # -----------------------------------------------------------------------
    if strength == "no_merit":
        opener = (
            f"Audit of {carrier}'s {charge_amt} {charge_type} charge on "
            f"{id_label} found no FMC Part 541 violations. "
            "All evaluated rules passed."
        )
        if cannot_evals:
            gap_ids = ", ".join(ce.rule_id for ce in cannot_evals)
            opener += (
                f" Note: {len(cannot_evals)} rule(s) could not be evaluated due to "
                f"insufficient evidence ({gap_ids}); additional evidence may uncover "
                "further grounds for dispute."
            )
        return opener

    # -----------------------------------------------------------------------
    # Violation path — group by confidence tier
    # -----------------------------------------------------------------------
    high_viols  = [v for v in violations if v.confidence >= 0.8]
    lower_viols = [v for v in violations if v.confidence < 0.8]

    def _describe(v: Violation) -> str:
        return f"{v.rule_id} ({v.rule_name})"

    parts: list[str] = []

    # Opening line — carrier, billed party, amount, identifier
    total_v = len(violations)
    parts.append(
        f"{carrier} billed {billed_party} {charge_amt} in {charge_type} "
        f"on {id_label}."
    )

    if high_viols:
        desc = "; ".join(_describe(v) for v in high_viols)
        parts.append(
            f"Audit found {total_v} FMC Part 541 violation(s) including "
            f"{len(high_viols)} high-confidence finding(s): {desc}."
        )
    elif lower_viols:
        desc = "; ".join(_describe(v) for v in lower_viols)
        parts.append(
            f"Audit found {total_v} FMC Part 541 violation(s): {desc}."
        )

    strength_phrases = {
        "strong":   "The dispute is well-supported by the regulatory record.",
        "moderate": "The dispute is moderately supported; key violations are documented.",
        "weak":     "The dispute is supported by low-confidence findings only; "
                    "additional evidence is recommended before proceeding.",
    }
    parts.append(strength_phrases[strength])

    if cannot_evals:
        gap_ids = ", ".join(ce.rule_id for ce in cannot_evals)
        parts.append(
            f"Additionally, {len(cannot_evals)} rule(s) could not be evaluated due to "
            f"missing evidence ({gap_ids}); obtaining that evidence may further "
            "strengthen the dispute."
        )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def _build_graph() -> StateGraph:
    """Construct and return the compiled ComplianceAuditor StateGraph."""
    graph = StateGraph(AuditState)

    # --- Gate node ---
    graph.add_node("gate", _gate_node)

    # --- Parallel rule nodes (R003–R012) ---
    for rid in _PARALLEL_RULE_IDS:
        graph.add_node(f"run_{rid.lower()}", _make_rule_node(rid))

    # --- Synthesize node ---
    graph.add_node("synthesize", _synthesize_node)

    # --- Edges ---
    # Entry point
    graph.add_edge(START, "gate")

    # Gate → conditional fan-out or short-circuit
    path_map: dict[str, str] = {"synthesize": "synthesize"}
    path_map.update({f"run_{rid.lower()}": f"run_{rid.lower()}" for rid in _PARALLEL_RULE_IDS})

    graph.add_conditional_edges("gate", _route_gate, path_map)

    # Each parallel rule node feeds synthesize
    for rid in _PARALLEL_RULE_IDS:
        graph.add_edge(f"run_{rid.lower()}", "synthesize")

    # Synthesize → END
    graph.add_edge("synthesize", END)

    return graph


# Module-level compiled graph — import and use directly in tests / Step 6.
audit_graph = _build_graph().compile()


# ---------------------------------------------------------------------------
# Public convenience wrapper
# ---------------------------------------------------------------------------

def run_audit(case: DisputeCase) -> ComplianceVerdict:
    """Run the full 12-rule compliance audit on a DisputeCase.

    This is the primary entry point for DisputeOps Stage 3.  It invokes the
    compiled LangGraph, waits for all parallel branches to complete, and
    returns the synthesized ComplianceVerdict.

    Args:
        case: The fully populated ``DisputeCase`` to audit.

    Returns:
        A frozen ``ComplianceVerdict`` containing violations, cannot-evaluate
        results, clean rule IDs, overall strength, and a demand-letter-ready
        summary.

    Example:
        >>> from agents.compliance_auditor.compliance_auditor import run_audit
        >>> verdict = run_audit(case)
        >>> verdict.overall_strength
        'strong'
        >>> [v.rule_id for v in verdict.violations]
        ['R001', 'R002']
    """
    logger.info("run_audit: starting audit for case %s", case.case_id)

    initial_state: AuditState = {
        "case": case,
        "results": {},
        "short_circuit": False,
        "verdict": None,
    }

    final_state = audit_graph.invoke(initial_state)

    verdict = final_state["verdict"]
    if verdict is None:
        raise RuntimeError(
            f"ComplianceAuditor graph did not produce a verdict for case {case.case_id!r}. "
            "Check that the 'synthesize' node ran and returned a ComplianceVerdict."
        )

    logger.info(
        "run_audit: completed case %s — overall_strength=%s",
        case.case_id, verdict.overall_strength,
    )
    return verdict
