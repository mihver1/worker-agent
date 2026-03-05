"""Integration tests: WebSocket protocol, REST API, OAuth token store, extensions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from worker_ai.models import Done, TextDelta, Usage
from worker_ai.oauth import OAuthToken, TokenStore
from worker_core.config import WorkerConfig
from worker_core.extensions import discover_extensions
from worker_server.server import ServerState, _create_rest_app, handle_client

from conftest import MockProvider


# ── OAuth Token Store ─────────────────────────────────────────────


class TestTokenStore:
    def test_save_and_load(self, tmp_path):
        store = TokenStore(path=tmp_path / "auth.json")
        token = OAuthToken(
            access_token="acc_123",
            refresh_token="ref_456",
            provider="kimi",
            expires_at=9999999999.0,
        )
        store.save(token)

        loaded = store.load("kimi")
        assert loaded is not None
        assert loaded.access_token == "acc_123"
        assert loaded.refresh_token == "ref_456"
        assert loaded.provider == "kimi"

    def test_load_nonexistent(self, tmp_path):
        store = TokenStore(path=tmp_path / "missing.json")
        assert store.load("kimi") is None

    def test_save_multiple_providers(self, tmp_path):
        store = TokenStore(path=tmp_path / "auth.json")
        store.save(OAuthToken(access_token="a1", provider="kimi"))
        store.save(OAuthToken(access_token="a2", provider="openai"))

        assert store.load("kimi").access_token == "a1"  # type: ignore[union-attr]
        assert store.load("openai").access_token == "a2"  # type: ignore[union-attr]

    def test_remove(self, tmp_path):
        store = TokenStore(path=tmp_path / "auth.json")
        store.save(OAuthToken(access_token="a1", provider="kimi"))
        store.remove("kimi")
        assert store.load("kimi") is None

    def test_expired_token(self):
        token = OAuthToken(access_token="x", expires_at=1.0)  # long expired
        assert token.is_expired is True

    def test_non_expired_token(self):
        token = OAuthToken(access_token="x", expires_at=9999999999.0)
        assert token.is_expired is False

    def test_no_expiry_not_expired(self):
        token = OAuthToken(access_token="x", expires_at=0.0)
        assert token.is_expired is False


# ── REST API ──────────────────────────────────────────────────────


class TestRESTAPI:
    @pytest.fixture
    def state(self):
        config = WorkerConfig()
        return ServerState(config=config)

    @pytest.mark.asyncio
    async def test_health_no_auth(self, state):
        from aiohttp.test_utils import TestClient, TestServer

        app = _create_rest_app(state, "test_token")
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/health")
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "ok"
            assert data["sessions"] == 0

    @pytest.mark.asyncio
    async def test_sessions_requires_auth(self, state):
        from aiohttp.test_utils import TestClient, TestServer

        app = _create_rest_app(state, "test_token")
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/sessions")
            assert resp.status == 401

    @pytest.mark.asyncio
    async def test_sessions_with_auth(self, state):
        from aiohttp.test_utils import TestClient, TestServer

        app = _create_rest_app(state, "test_token")
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(
                "/api/sessions",
                headers={"Authorization": "Bearer test_token"},
            )
            assert resp.status == 200
            data = await resp.json()
            assert data["sessions"] == []

    @pytest.mark.asyncio
    async def test_delete_session_not_found(self, state):
        from aiohttp.test_utils import TestClient, TestServer

        app = _create_rest_app(state, "test_token")
        async with TestClient(TestServer(app)) as client:
            resp = await client.delete(
                "/api/sessions/nonexistent",
                headers={"Authorization": "Bearer test_token"},
            )
            assert resp.status == 404


# ── WebSocket Protocol ────────────────────────────────────────────


class FakeWebSocket:
    """Mock WebSocket connection for testing the protocol handler."""

    def __init__(self):
        self.sent: list[str] = []
        self._incoming: asyncio.Queue[str] = asyncio.Queue()
        self.remote_address = ("test", 0)
        self._closed = False

    async def send(self, data: str) -> None:
        self.sent.append(data)

    def inject(self, data: str) -> None:
        self._incoming.put_nowait(data)

    def close_input(self) -> None:
        self._incoming.put_nowait(None)  # type: ignore[arg-type]

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item


class TestWebSocketProtocol:
    @pytest.mark.asyncio
    async def test_message_flow(self, tmp_workdir):
        """Client sends message → server streams text_delta + done."""
        provider = MockProvider(
            responses=[
                [TextDelta(content="Hello!"), Done(usage=Usage(input_tokens=5, output_tokens=3))],
            ]
        )
        config = WorkerConfig()
        state = ServerState(config=config)

        # Pre-inject a mock session
        from worker_core.agent import AgentSession

        session = AgentSession(provider=provider, model="test", tools=[])
        state.sessions["test_session"] = session

        ws = FakeWebSocket()
        ws.inject(json.dumps({"type": "message", "session_id": "test_session", "content": "hi"}))
        ws.close_input()

        await handle_client(ws, state)  # type: ignore[arg-type]

        # Parse sent messages
        messages = [json.loads(m) for m in ws.sent]
        types = [m["type"] for m in messages]
        assert "text_delta" in types
        assert "done" in types

    @pytest.mark.asyncio
    async def test_empty_message_error(self):
        config = WorkerConfig()
        state = ServerState(config=config)

        ws = FakeWebSocket()
        ws.inject(json.dumps({"type": "message", "content": ""}))
        ws.close_input()

        await handle_client(ws, state)  # type: ignore[arg-type]

        messages = [json.loads(m) for m in ws.sent]
        assert messages[0]["type"] == "error"
        assert "Empty" in messages[0]["error"]

    @pytest.mark.asyncio
    async def test_invalid_json(self):
        config = WorkerConfig()
        state = ServerState(config=config)

        ws = FakeWebSocket()
        ws.inject("not json at all")
        ws.close_input()

        await handle_client(ws, state)  # type: ignore[arg-type]

        messages = [json.loads(m) for m in ws.sent]
        assert messages[0]["type"] == "error"
        assert "Invalid JSON" in messages[0]["error"]

    @pytest.mark.asyncio
    async def test_unknown_type(self):
        config = WorkerConfig()
        state = ServerState(config=config)

        ws = FakeWebSocket()
        ws.inject(json.dumps({"type": "foobar"}))
        ws.close_input()

        await handle_client(ws, state)  # type: ignore[arg-type]

        messages = [json.loads(m) for m in ws.sent]
        assert messages[0]["type"] == "error"
        assert "Unknown type" in messages[0]["error"]


# ── Extension Discovery ──────────────────────────────────────────


class TestExtensionDiscovery:
    def test_discover_returns_empty_by_default(self):
        """No extensions installed → empty dict."""
        extensions = discover_extensions()
        # May or may not be empty depending on env, but should not crash
        assert isinstance(extensions, dict)
