"""Unit tests for agents/shared/llm_client.py.

5 tests:
  1. Returns a ChatOpenAI instance
  2. Env-var COMPLIANCE_AUDITOR_MODEL is respected
  3. Default model is gpt-4o-mini when env var unset
  4. Empty purpose raises ValueError
  5. Mock fixture works (chain.invoke is patchable)
"""

from __future__ import annotations

import os

import pytest
from unittest.mock import MagicMock, patch

from agents.shared.llm_client import get_llm_client

# ChatOpenAI requires OPENAI_API_KEY to instantiate (even without making calls).
# Use a dummy key so tests don't need a live credential.
_DUMMY_KEY = "sk-test-0000000000000000000000000000000000000000000000"


def test_returns_chat_openai_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_llm_client returns a ChatOpenAI object."""
    monkeypatch.setenv("OPENAI_API_KEY", _DUMMY_KEY)
    from langchain_openai import ChatOpenAI
    client = get_llm_client(purpose="test_return_type")
    assert isinstance(client, ChatOpenAI)


def test_env_var_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """COMPLIANCE_AUDITOR_MODEL env var overrides the default model."""
    monkeypatch.setenv("OPENAI_API_KEY", _DUMMY_KEY)
    monkeypatch.setenv("COMPLIANCE_AUDITOR_MODEL", "gpt-4o")
    client = get_llm_client(purpose="test_env_var")
    assert client.model_name == "gpt-4o"


def test_default_model_is_gpt4o_mini(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default model is gpt-4o-mini when env var is unset."""
    monkeypatch.setenv("OPENAI_API_KEY", _DUMMY_KEY)
    monkeypatch.delenv("COMPLIANCE_AUDITOR_MODEL", raising=False)
    client = get_llm_client(purpose="test_default_model")
    assert client.model_name == "gpt-4o-mini"


@pytest.mark.parametrize("bad_purpose", ["", "   ", "\t"])
def test_empty_purpose_raises_value_error(bad_purpose: str) -> None:
    """Empty or whitespace-only purpose raises ValueError."""
    with pytest.raises(ValueError, match="purpose"):
        get_llm_client(purpose=bad_purpose)


def test_mock_fixture_works(mocker) -> None:
    """LLM client can be mocked at the call site using pytest-mock."""
    mock_client = MagicMock()
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = MagicMock(
        violated=True, confidence=0.85, reasoning="Mocked reasoning."
    )
    mock_client.with_structured_output.return_value = mock_chain

    mocker.patch("agents.shared.llm_client.get_llm_client", return_value=mock_client)

    from agents.shared.llm_client import get_llm_client as patched_get_llm_client
    client = patched_get_llm_client(purpose="test_mock")
    chain = client.with_structured_output(object)
    result = chain.invoke([])

    assert result.violated is True
    assert result.confidence == 0.85
    assert "Mocked" in result.reasoning
