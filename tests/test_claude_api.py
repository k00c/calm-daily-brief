"""Tests for the Claude API integration in generate.py."""
from unittest.mock import MagicMock, patch

import pytest

import generate

CANDIDATES = [
    {
        "source": "ABC News",
        "category": "au",
        "is_longform": False,
        "title": "Test Story",
        "summary": "A brief summary.",
        "link": "https://example.com/story",
    }
]

STORIES = [
    {
        "topic": "Employment",
        "card_type": "news",
        "source": "ABC News",
        "link": "https://example.com/story",
        "teaser": "A calm sentence.",
        "full_content": "Content here.",
        "tag": "awareness",
    }
]


def _mock_response(stories):
    block = MagicMock()
    block.type = "tool_use"
    block.name = "publish_digest"
    block.input = {"stories": stories}
    response = MagicMock()
    response.content = [block]
    return response


def _text_only_response():
    block = MagicMock()
    block.type = "text"
    response = MagicMock()
    response.content = [block]
    return response


def test_select_and_rewrite_returns_stories(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_response(STORIES)
    with patch("anthropic.Anthropic", return_value=mock_client):
        result = generate.select_and_rewrite(CANDIDATES)
    assert result == STORIES


def test_select_and_rewrite_calls_api_once(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_response(STORIES)
    with patch("anthropic.Anthropic", return_value=mock_client):
        generate.select_and_rewrite(CANDIDATES)
    mock_client.messages.create.assert_called_once()


def test_select_and_rewrite_uses_correct_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_response(STORIES)
    with patch("anthropic.Anthropic", return_value=mock_client):
        generate.select_and_rewrite(CANDIDATES)
    kwargs = mock_client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-haiku-4-5-20251001"


def test_select_and_rewrite_uses_publish_digest_tool(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_response(STORIES)
    with patch("anthropic.Anthropic", return_value=mock_client):
        generate.select_and_rewrite(CANDIDATES)
    kwargs = mock_client.messages.create.call_args.kwargs
    assert kwargs["tool_choice"]["name"] == "publish_digest"


def test_select_and_rewrite_raises_when_no_tool_call(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _text_only_response()
    with patch("anthropic.Anthropic", return_value=mock_client):
        with pytest.raises(RuntimeError, match="publish_digest"):
            generate.select_and_rewrite(CANDIDATES)


def test_select_and_rewrite_embeds_reader_context(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("READER_CONTEXT", "Interested in marine biology")
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_response(STORIES)
    with patch("anthropic.Anthropic", return_value=mock_client):
        generate.select_and_rewrite(CANDIDATES)
    kwargs = mock_client.messages.create.call_args.kwargs
    assert "marine biology" in kwargs["system"]


def test_select_and_rewrite_uses_default_context_when_env_missing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("READER_CONTEXT", raising=False)
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_response(STORIES)
    with patch("anthropic.Anthropic", return_value=mock_client):
        generate.select_and_rewrite(CANDIDATES)
    kwargs = mock_client.messages.create.call_args.kwargs
    assert "No specific reader context" in kwargs["system"]


def test_select_and_rewrite_includes_candidates_in_message(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_response(STORIES)
    with patch("anthropic.Anthropic", return_value=mock_client):
        generate.select_and_rewrite(CANDIDATES)
    kwargs = mock_client.messages.create.call_args.kwargs
    user_content = kwargs["messages"][0]["content"]
    assert "Test Story" in user_content
