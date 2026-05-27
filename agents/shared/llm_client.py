"""Shared LangChain LLM client factory for ComplianceAuditor agents.

Single source of truth for model selection, temperature, retry behaviour,
and timeout.  Rules call ``get_llm_client(purpose=...)`` and receive a
``ChatOpenAI`` instance ready for ``.with_structured_output()``.

Design decisions:
  - No module-level singleton.  ``get_llm_client()`` returns a fresh instance
    per call.  LangChain clients are lightweight; sharing them causes test
    flakiness when mocking.
  - ``purpose`` is mandatory and non-empty.  Every log line emitted by the
    LLM client carries the purpose string so operations can grep logs by
    which rule made which call.
  - Model falls back through: explicit arg → COMPLIANCE_AUDITOR_MODEL env var
    → ``"gpt-4o-mini"``.
  - ``max_retries`` uses LangChain's built-in tenacity retry mechanism.
    Note: LangChain 0.2+ retries on RateLimitError and APIConnectionError
    but not on InvalidRequestError (4xx bad-request class) — no need to
    roll a custom retry filter.
"""

from __future__ import annotations

import logging
import os

from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

_DEFAULT_MODEL: str = "gpt-4o-mini"
_ENV_VAR: str = "COMPLIANCE_AUDITOR_MODEL"


def get_llm_client(
    *,
    purpose: str,
    model: str | None = None,
    temperature: float = 0.0,
    max_retries: int = 2,
    timeout_seconds: float = 30.0,
) -> ChatOpenAI:
    """Return a configured ``ChatOpenAI`` client for a named rule purpose.

    Args:
        purpose: Non-empty identifier for the calling rule, e.g.
            ``"R002_basis_quality"``.  Appears in every log line.
        model: Override the model name.  Defaults to the
            ``COMPLIANCE_AUDITOR_MODEL`` env var, falling back to
            ``"gpt-4o-mini"``.
        temperature: Sampling temperature.  ``0.0`` for deterministic output.
        max_retries: Number of LangChain-managed retries on transient errors.
        timeout_seconds: Per-request timeout in seconds.

    Returns:
        A ``ChatOpenAI`` instance.  Call ``.with_structured_output(Schema)``
        on it before invoking.

    Raises:
        ValueError: If ``purpose`` is empty or whitespace.

    Example:
        >>> llm = get_llm_client(purpose="R007_force_majeure")
        >>> chain = llm.with_structured_output(MySchema)
        >>> result = chain.invoke(messages)
    """
    if not purpose or not purpose.strip():
        raise ValueError("purpose must be a non-empty string")

    resolved_model = model or os.getenv(_ENV_VAR, _DEFAULT_MODEL)
    logger.info(
        "LLM client: purpose=%r model=%r temperature=%.1f "
        "max_retries=%d timeout=%.1fs",
        purpose, resolved_model, temperature, max_retries, timeout_seconds,
    )

    return ChatOpenAI(
        model=resolved_model,
        temperature=temperature,
        max_retries=max_retries,
        timeout=timeout_seconds,
    )
