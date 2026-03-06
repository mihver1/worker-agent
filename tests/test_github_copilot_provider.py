"""Tests for GitHub Copilot provider behavior."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from worker_ai.models import Done, Message, Role, TextDelta
from worker_ai.providers import create_default_registry
from worker_ai.providers.github_copilot import GitHubCopilotProvider


def _sse_lines(events: list[dict]) -> list[str]:
    return [f"data: {json.dumps(event)}" for event in events] + ["data: [DONE]"]


class TestGitHubCopilotProviderRegistry:
    @pytest.mark.asyncio
    async def test_registry_exposes_dedicated_github_copilot_provider(self):
        registry = create_default_registry()

        provider = registry.create("github_copilot", api_key="gho_token_123")

        assert isinstance(provider, GitHubCopilotProvider)
        assert provider.list_models()[0].provider == "github_copilot"

        await provider.close()


class TestGitHubCopilotProviderRuntime:
    @pytest.mark.asyncio
    async def test_stream_chat_uses_copilot_endpoint_and_bearer_auth(self):
        provider = GitHubCopilotProvider(api_key="gho_token_123")

        events = [
            {
                "choices": [
                    {
                        "delta": {"content": "Hello"},
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        ]

        mock_response = AsyncMock()
        mock_response.status_code = 200

        async def async_lines():
            for line in _sse_lines(events):
                yield line

        mock_response.aiter_lines = async_lines
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(provider._client, "stream", return_value=mock_cm) as mock_stream:
            collected = []
            async for event in provider.stream_chat(
                "gpt-4.1",
                [Message(role=Role.USER, content="Hi")],
            ):
                collected.append(event)

        assert provider._base_url == "https://api.githubcopilot.com"
        assert isinstance(collected[0], TextDelta)
        assert collected[0].content == "Hello"
        assert isinstance(collected[1], Done)
        assert collected[1].usage.input_tokens == 5
        assert collected[1].usage.output_tokens == 2

        assert mock_stream.call_args.args == ("POST", "/chat/completions")
        body = mock_stream.call_args.kwargs["json"]
        headers = mock_stream.call_args.kwargs["headers"]
        assert body["model"] == "gpt-4.1"
        assert body["messages"] == [{"role": "user", "content": "Hi"}]
        assert body["stream_options"] == {"include_usage": True}
        assert headers["authorization"] == "Bearer gho_token_123"

        await provider.close()
