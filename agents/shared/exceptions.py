"""Shared exception types for DisputeOps ComplianceAuditor agents.

All custom exceptions live here so that LangGraph node exception handlers
can import from a single, stable location without creating circular imports
between agent sub-packages.
"""

from __future__ import annotations


class CitationNotFoundError(KeyError):
    """Raised when ``get_citation()`` is called with an unrecognised rule ID.

    Inherits from ``KeyError`` so callers that treat citation lookups like
    dict access get familiar exception semantics, while still being catchable
    by name for targeted handling in the rules engine and demand-letter
    generator.

    Attributes:
        rule_id: The unrecognised rule ID that was requested.

    Example:
        >>> try:
        ...     get_citation("R999")
        ... except CitationNotFoundError as exc:
        ...     print(exc.rule_id)
        R999
    """

    def __init__(self, rule_id: str) -> None:
        self.rule_id = rule_id
        super().__init__(f"No citation registered for rule ID {rule_id!r}")
