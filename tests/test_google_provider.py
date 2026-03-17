"""Tests for Google Gemini providers and Vertex split behavior."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from artel_ai.models import Done, Message, Role, TextDelta
from artel_ai.providers import create_default_registry
from artel_ai.providers.google import GoogleProvider, GoogleVertexProvider


class TestGoogleProviderSplit:
    @pytest.mark.asyncio
    async def test_registry_separates_google_and_google_vertex(self):
        registry = create_default_registry()

        google_provider = registry.create("google", api_key="gemini-key")
        vertex_provider = registry.create("google_vertex", project="demo-project")

        assert isinstance(google_provider, GoogleProvider)
        assert isinstance(vertex_provider, GoogleVertexProvider)
        assert google_provider.list_models()[0].provider == "google"
        assert vertex_provider.list_models()[0].provider == "google_vertex"

        await google_provider.close()
        await vertex_provider.close()


class TestGoogleVertexProvider:
    @pytest.mark.asyncio
    async def test_stream_chat_uses_vertex_endpoint_and_bearer_auth(self):
        provider = GoogleVertexProvider(
            project="demo-project",
            location="us-central1",
        )
        stream_payload = json.dumps(
            [
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": "Hello"}],
                            }
                        }
                    ]
                },
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": " world"}],
                            },
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 3,
                        "candidatesTokenCount": 2,
                    },
                },
            ]
        )

        mock_response = AsyncMock()
        mock_response.status_code = 200

        async def async_text():
            midpoint = len(stream_payload) // 2
            yield stream_payload[:midpoint]
            yield stream_payload[midpoint:]

        mock_response.aiter_text = async_text
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(
                provider,
                "_resolve_access_token",
                return_value=("vertex-token", "demo-project"),
            ),
            patch.object(provider._client, "stream", return_value=mock_cm) as mock_stream,
        ):
            collected = []
            async for event in provider.stream_chat(
                "gemini-2.5-pro",
                [Message(role=Role.USER, content="Hi")],
            ):
                collected.append(event)

        assert isinstance(collected[0], TextDelta)
        assert collected[0].content == "Hello"
        assert isinstance(collected[1], TextDelta)
        assert collected[1].content == " world"
        assert isinstance(collected[2], Done)
        assert collected[2].usage.input_tokens == 3
        assert collected[2].usage.output_tokens == 2

        assert mock_stream.call_args.args == (
            "POST",
            "https://us-central1-aiplatform.googleapis.com/v1/projects/demo-project/"
            "locations/us-central1/publishers/google/models/gemini-2.5-pro:"
            "streamGenerateContent",
        )
        headers = mock_stream.call_args.kwargs["headers"]
        body = mock_stream.call_args.kwargs["json"]
        assert headers["authorization"] == "Bearer vertex-token"
        assert body["contents"] == [{"role": "user", "parts": [{"text": "Hi"}]}]

        await provider.close()
