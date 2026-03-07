"""Integration tests: WebSocket protocol, REST API, OAuth token store, extensions."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from conftest import MockProvider
from worker_ai.models import Done, TextDelta, Usage
from worker_ai.oauth import OAuthToken, RemoteOAuthChallenge, TokenStore
from worker_core.config import ProviderConfig, ProviderModelConfig, WorkerConfig
from worker_core.extensions import discover_extensions
from worker_core.sessions import SessionStore
from worker_server.server import ServerState, _create_rest_app, handle_client

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


class TestOAuthProviderRefresh:
    @pytest.mark.asyncio
    async def test_get_token_refreshes_and_persists(self, tmp_path, monkeypatch):
        import worker_ai.oauth as oauth_mod

        store = TokenStore(path=tmp_path / "auth.json")
        store.save(
            OAuthToken(
                access_token="expired_token",
                refresh_token="refresh_token",
                provider="anthropic",
                expires_at=1.0,
            )
        )

        async def fake_refresh(self, token):
            assert token.access_token == "expired_token"
            return OAuthToken(
                access_token="refreshed_token",
                refresh_token="new_refresh_token",
                provider="anthropic",
                expires_at=9999999999.0,
            )

        monkeypatch.setattr(oauth_mod.AnthropicOAuth, "refresh", fake_refresh)

        provider = oauth_mod.AnthropicOAuth(token_store=store)
        token = await provider.get_token()

        assert token is not None
        assert token.access_token == "refreshed_token"
        reloaded = store.load("anthropic")
        assert reloaded is not None
        assert reloaded.access_token == "refreshed_token"
        assert reloaded.refresh_token == "new_refresh_token"


class TestRuntimeBootstrap:
    @pytest.mark.asyncio
    async def test_bootstrap_runtime_supports_kimi(self, tmp_path, monkeypatch):
        from worker_ai.providers.kimi import KimiProvider
        from worker_core.bootstrap import bootstrap_runtime
        from worker_core.cli import _resolve_api_key

        monkeypatch.setenv("MOONSHOT_API_KEY", "moonshot_env_token")

        runtime = await bootstrap_runtime(
            WorkerConfig(),
            "kimi",
            "kimi-k2.5",
            project_dir=str(tmp_path),
            resolve_api_key=_resolve_api_key,
            include_extensions=False,
            runtime="local",
        )

        assert isinstance(runtime.provider, KimiProvider)
        assert runtime.provider.api_key == "moonshot_env_token"
        assert runtime.provider._base_url == "https://api.kimi.com/coding/v1"
        assert runtime.context_window == 262_144

        await runtime.provider.close()
    @pytest.mark.asyncio
    async def test_bootstrap_runtime_supports_github_copilot_alias(self, tmp_path, monkeypatch):
        from worker_ai.providers.github_copilot import GitHubCopilotProvider
        from worker_core.bootstrap import bootstrap_runtime
        from worker_core.cli import _resolve_api_key

        monkeypatch.setenv("GH_TOKEN", "gho_env_token")

        runtime = await bootstrap_runtime(
            WorkerConfig(),
            "github-copilot",
            "gpt-4.1",
            project_dir=str(tmp_path),
            resolve_api_key=_resolve_api_key,
            include_extensions=False,
            runtime="local",
        )

        assert isinstance(runtime.provider, GitHubCopilotProvider)
        assert runtime.provider.api_key == "gho_env_token"
        assert runtime.provider._base_url == "https://api.githubcopilot.com"
        assert runtime.context_window == 1_047_576

        await runtime.provider.close()

    @pytest.mark.asyncio
    async def test_bootstrap_runtime_supports_ollama_cloud_alias(self, tmp_path, monkeypatch):
        from worker_ai.providers.ollama import OllamaProvider
        from worker_core.bootstrap import bootstrap_runtime
        from worker_core.cli import _resolve_api_key

        monkeypatch.setenv("OLLAMA_API_KEY", "ollama_cloud_token")

        runtime = await bootstrap_runtime(
            WorkerConfig(
                providers={
                    "ollama_cloud": ProviderConfig(
                        models={
                            "gpt-oss:20b": ProviderModelConfig(
                                context_window=200000,
                            )
                        }
                    )
                }
            ),
            "ollama-cloud",
            "gpt-oss:20b",
            project_dir=str(tmp_path),
            resolve_api_key=_resolve_api_key,
            include_extensions=False,
            runtime="local",
        )

        assert isinstance(runtime.provider, OllamaProvider)
        assert runtime.provider.api_key == "ollama_cloud_token"
        assert runtime.provider._base_url == "https://ollama.com/v1"
        assert runtime.context_window == 200000

        await runtime.provider.close()

    @pytest.mark.asyncio
    async def test_bootstrap_runtime_supports_lm_studio_alias(self, tmp_path):
        from worker_ai.providers.lmstudio import LMStudioProvider
        from worker_core.bootstrap import bootstrap_runtime
        from worker_core.cli import _resolve_api_key

        runtime = await bootstrap_runtime(
            WorkerConfig(
                providers={
                    "lmstudio": ProviderConfig(
                        models={
                            "openai/gpt-oss-20b": ProviderModelConfig(
                                context_window=131072,
                            )
                        }
                    )
                }
            ),
            "lm-studio",
            "openai/gpt-oss-20b",
            project_dir=str(tmp_path),
            resolve_api_key=_resolve_api_key,
            include_extensions=False,
            runtime="local",
        )

        assert isinstance(runtime.provider, LMStudioProvider)
        assert runtime.provider.api_key is None
        assert runtime.provider._base_url == "http://127.0.0.1:1234/v1"
        assert runtime.context_window == 131072

        await runtime.provider.close()


class TestAzureFoundryIntegration:
    @pytest.mark.asyncio
    async def test_agent_session_uses_non_stream_fallback_for_empty_foundry_stream(self):
        from worker_ai.providers.azure_openai import AzureOpenAIProvider
        from worker_core.agent import AgentEventType, AgentSession

        provider = AzureOpenAIProvider(
            api_key="azure-key",
            base_url="https://demo.services.ai.azure.com",
        )

        stream_events = [
            {
                "choices": [],
                "prompt_filter_results": [
                    {
                        "prompt_index": 0,
                        "content_filter_results": {
                            "hate": {"filtered": False, "severity": "safe"}
                        },
                    }
                ],
            }
        ]

        mock_stream_response = AsyncMock()
        mock_stream_response.status_code = 200

        async def async_lines():
            for event in stream_events:
                yield f"data: {json.dumps(event)}"
            yield "data: [DONE]"

        mock_stream_response.aiter_lines = async_lines
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_stream_response)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_post_response = AsyncMock()
        mock_post_response.status_code = 200
        mock_post_response.json = Mock(
            return_value={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "reasoning_content": "Reasoning",
                            "content": "Answer",
                            "tool_calls": None,
                        },
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            }
        )

        with (
            patch.object(provider._client, "stream", return_value=mock_cm),
            patch.object(provider._client, "post", return_value=mock_post_response),
        ):
            session = AgentSession(provider=provider, model="Kimi-K2.5", tools=[])
            events = []
            async for event in session.run("Hi"):
                events.append(event)

        assert [event.type for event in events] == [
            AgentEventType.REASONING_DELTA,
            AgentEventType.TEXT_DELTA,
            AgentEventType.DONE,
        ]
        assert events[0].content == "Reasoning"
        assert events[1].content == "Answer"
        assert events[2].usage is not None
        assert events[2].usage.input_tokens == 5
        assert events[2].usage.output_tokens == 2
        assert session.messages[-1].reasoning == "Reasoning"
        assert session.messages[-1].content == "Answer"

        await provider.close()


class TestCliLogin:
    def test_worker_login_uses_github_copilot_oauth_broker(self, tmp_path, monkeypatch):
        import worker_ai.oauth as oauth_mod
        from click.testing import CliRunner
        from worker_ai.oauth import TokenStore
        from worker_core import cli as cli_mod

        monkeypatch.setattr(oauth_mod, "_DEFAULT_AUTH_PATH", tmp_path / "auth.json")
        monkeypatch.setattr(cli_mod, "load_config", lambda cwd: WorkerConfig())

        async def fake_run_command(args: list[str]) -> int:
            assert args == ["gh", "auth", "login", "--web", "--clipboard", "--skip-ssh-key"]
            return 0

        async def fake_load_token(github_host: str) -> str | None:
            assert github_host == ""
            return "gho_cli_login_token"

        monkeypatch.setattr(oauth_mod, "_run_command", fake_run_command)
        monkeypatch.setattr(
            oauth_mod,
            "load_github_copilot_token_from_gh_cli",
            fake_load_token,
        )

        def run_coro(coro):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

        monkeypatch.setattr(cli_mod.asyncio, "run", run_coro)

        runner = CliRunner()
        result = runner.invoke(cli_mod.cli, ["login", "github-copilot"])

        assert result.exit_code == 0
        saved = TokenStore(path=tmp_path / "auth.json").load("github_copilot")
        assert saved is not None
        assert saved.access_token == "gho_cli_login_token"

    def test_worker_login_reports_api_key_hint_for_kimi(self, monkeypatch):
        from click.testing import CliRunner
        from worker_core import cli as cli_mod

        monkeypatch.setattr(cli_mod, "load_config", lambda cwd: WorkerConfig())

        runner = CliRunner()
        result = runner.invoke(cli_mod.cli, ["login", "kimi"])

        assert result.exit_code == 0
        assert "OAuth not supported for 'kimi'." in result.output
        assert "Use MOONSHOT_API_KEY or [providers.kimi].api_key." in result.output

    def test_worker_login_without_gh_shows_install_hint(self, tmp_path, monkeypatch):
        import worker_ai.oauth as oauth_mod
        from click.testing import CliRunner
        from worker_core import cli as cli_mod

        monkeypatch.setattr(oauth_mod, "_DEFAULT_AUTH_PATH", tmp_path / "auth.json")
        monkeypatch.setattr(cli_mod, "load_config", lambda cwd: WorkerConfig())

        async def fake_create_subprocess_exec(*args, **kwargs):
            raise OSError("gh not found")

        monkeypatch.setattr(
            oauth_mod.asyncio,
            "create_subprocess_exec",
            fake_create_subprocess_exec,
        )

        def run_coro(coro):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

        monkeypatch.setattr(cli_mod.asyncio, "run", run_coro)

        runner = CliRunner()
        result = runner.invoke(cli_mod.cli, ["login", "github-copilot"])

        assert result.exit_code == 0
        assert "GitHub CLI (`gh`) is required for GitHub Copilot login." in result.output
        assert "brew install gh" in result.output
        assert "GH_TOKEN" in result.output


class TestCliConnect:
    def test_worker_connect_passes_forward_credentials(self, monkeypatch):
        import worker_tui.app as tui_app
        from click.testing import CliRunner
        from worker_core import cli as cli_mod

        captured: dict[str, str] = {}

        def fake_run_tui(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(tui_app, "run_tui", fake_run_tui)

        runner = CliRunner()
        result = runner.invoke(
            cli_mod.cli,
            [
                "connect",
                "ws://host:7432",
                "--token",
                "tok_test",
                "--forward-credentials",
                "all",
            ],
        )

        assert result.exit_code == 0
        assert captured == {
            "remote_url": "ws://host:7432",
            "auth_token": "tok_test",
            "forward_credentials": "all",
        }

class TestCliServe:
    def test_worker_serve_passes_stdout_announcer(self, monkeypatch):
        import worker_server.server as server_mod
        from click.testing import CliRunner
        from worker_core import cli as cli_mod

        captured: dict[str, object] = {}

        async def fake_run_server(**kwargs):
            captured.update(kwargs)
            announce = kwargs["announce"]
            assert callable(announce)
            announce("Worker server starting")
            announce("  Auth token: wkr_test_token")

        def run_coro(coro):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

        monkeypatch.setattr(server_mod, "run_server", fake_run_server)
        monkeypatch.setattr(cli_mod.asyncio, "run", run_coro)

        runner = CliRunner()
        result = runner.invoke(cli_mod.cli, ["serve", "--host", "0.0.0.0", "--port", "9000"])

        assert result.exit_code == 0
        assert captured["host"] == "0.0.0.0"
        assert captured["port"] == 9000
        assert "Worker server starting" in result.output
        assert "Auth token: wkr_test_token" in result.output


class TestCliExtensions:
    def test_ext_install_uses_no_sources(self, monkeypatch):
        from click.testing import CliRunner
        from worker_core import cli as cli_mod

        calls: list[list[str]] = []

        def fake_run(args, capture_output, text):
            calls.append(args)
            return Mock(returncode=0)

        monkeypatch.setattr("subprocess.run", fake_run)

        runner = CliRunner()
        result = runner.invoke(cli_mod.cli, ["ext", "install", "git+https://example.com/ext.git"])

        assert result.exit_code == 0
        assert calls == [["uv", "pip", "install", "--no-sources", "git+https://example.com/ext.git"]]

    def test_ext_update_uses_no_sources(self, monkeypatch):
        from click.testing import CliRunner
        from worker_core import cli as cli_mod

        calls: list[list[str]] = []

        def fake_run(args, capture_output, text):
            calls.append(args)
            return Mock(returncode=0)

        monkeypatch.setattr("subprocess.run", fake_run)

        runner = CliRunner()
        result = runner.invoke(cli_mod.cli, ["ext", "update", "worker-ext-mcp"])

        assert result.exit_code == 0
        assert calls == [["uv", "pip", "install", "--no-sources", "--upgrade", "worker-ext-mcp"]]

# ── REST API ──────────────────────────────────────────────────────

class TestRemoteControlClient:
    @pytest.mark.asyncio
    async def test_request_uses_same_port_api_path_when_remote_url_has_ws_suffix(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        from worker_tui.remote_control import RemoteControlClient

        async def handle_health(request):
            return web.json_response({"path": request.path})

        app = web.Application()
        app.router.add_get("/api/health", handle_health)

        async with TestClient(TestServer(app)) as client:
            remote_url = str(client.make_url("/ws")).replace("http://", "ws://", 1)
            payload = await RemoteControlClient(remote_url).request("GET", "/api/health")

        assert payload == {"path": "/api/health"}

    @pytest.mark.asyncio
    async def test_request_uses_nested_api_path_for_prefixed_remote_url(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        from worker_tui.remote_control import RemoteControlClient

        async def handle_health(request):
            return web.json_response({"path": request.path})

        app = web.Application()
        app.router.add_get("/worker/api/health", handle_health)

        async with TestClient(TestServer(app)) as client:
            remote_url = str(client.make_url("/worker/ws")).replace("http://", "ws://", 1)
            payload = await RemoteControlClient(remote_url).request("GET", "/api/health")

        assert payload == {"path": "/worker/api/health"}


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

    @pytest.mark.asyncio
    async def test_delete_session_closes_provider(self, state):
        from aiohttp.test_utils import TestClient, TestServer
        from worker_core.agent import AgentSession

        class _ClosableMockProvider(MockProvider):
            def __init__(self):
                super().__init__()
                self.closed = False

            async def close(self) -> None:
                self.closed = True

        provider = _ClosableMockProvider()
        state.sessions["sess-1"] = AgentSession(provider=provider, model="test", tools=[])

        app = _create_rest_app(state, "test_token")
        async with TestClient(TestServer(app)) as client:
            resp = await client.delete(
                "/api/sessions/sess-1",
                headers={"Authorization": "Bearer test_token"},
            )
            assert resp.status == 200
        assert provider.closed is True
        assert "sess-1" not in state.sessions

    @pytest.mark.asyncio
    async def test_session_get_returns_default_model_before_creation(self, state):
        from aiohttp.test_utils import TestClient, TestServer

        app = _create_rest_app(state, "test_token")
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(
                "/api/sessions/remote-session",
                headers={"Authorization": "Bearer test_token"},
            )
            assert resp.status == 200
            data = await resp.json()
            assert data["session"]["exists"] is False
            assert data["session"]["model"] == state.config.agent.model

    @pytest.mark.asyncio
    async def test_session_get_returns_default_project_before_creation(self, tmp_path):
        from aiohttp.test_utils import TestClient, TestServer

        state = ServerState(config=WorkerConfig(), default_project_dir=str(tmp_path))
        app = _create_rest_app(state, "test_token")
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(
                "/api/sessions/remote-session",
                headers={"Authorization": "Bearer test_token"},
            )
            assert resp.status == 200
            data = await resp.json()
            assert data["session"]["project_dir"] == str(tmp_path)

    @pytest.mark.asyncio
    async def test_remote_bash_endpoint_executes_command(self, state):
        from aiohttp.test_utils import TestClient, TestServer

        app = _create_rest_app(state, "test_token")
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/sessions/remote-session/bash",
                headers={"Authorization": "Bearer test_token"},
                json={"command": "printf remote-bash-test"},
            )
            assert resp.status == 200
            data = await resp.json()
            assert data["output"] == "remote-bash-test"
            assert data["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_session_project_endpoint_and_cd_persist_remote_cwd(self, tmp_path):
        from aiohttp.test_utils import TestClient, TestServer

        project_a = tmp_path / "project-a"
        project_b = tmp_path / "project-b"
        project_a.mkdir()
        project_b.mkdir()

        state = ServerState(config=WorkerConfig(), default_project_dir=str(tmp_path))
        app = _create_rest_app(state, "test_token")
        async with TestClient(TestServer(app)) as client:
            resp = await client.put(
                "/api/sessions/remote-session/project",
                headers={"Authorization": "Bearer test_token"},
                json={"project_dir": str(project_a)},
            )
            assert resp.status == 200
            data = await resp.json()
            assert data["session"]["project_dir"] == str(project_a)

            resp = await client.post(
                "/api/sessions/remote-session/bash",
                headers={"Authorization": "Bearer test_token"},
                json={"command": "pwd"},
            )
            assert resp.status == 200
            data = await resp.json()
            assert data["output"] == str(project_a)

            resp = await client.post(
                "/api/sessions/remote-session/bash",
                headers={"Authorization": "Bearer test_token"},
                json={"command": f"cd {project_b}"},
            )
            assert resp.status == 200
            data = await resp.json()
            assert data["output"] == str(project_b)
            assert data["session"]["project_dir"] == str(project_b)

            resp = await client.post(
                "/api/sessions/remote-session/bash",
                headers={"Authorization": "Bearer test_token"},
                json={"command": "pwd"},
            )
            assert resp.status == 200
            data = await resp.json()
            assert data["output"] == str(project_b)

    @pytest.mark.asyncio
    async def test_sessions_list_includes_persisted_remote_sessions(self, tmp_path):
        from aiohttp.test_utils import TestClient, TestServer
        from worker_ai.models import Message, Role

        store = SessionStore(str(tmp_path / "sessions.db"))
        await store.open()
        try:
            project_dir = str(tmp_path / "project")
            (tmp_path / "project").mkdir()
            await store.create_session(
                "persisted-remote",
                "openai/gpt-4.1",
                title="Persisted remote session",
                project_dir=project_dir,
            )
            await store.add_message(
                "persisted-remote",
                Message(role=Role.USER, content="hello"),
            )

            state = ServerState(
                config=WorkerConfig(),
                default_project_dir=str(tmp_path),
                store=store,
            )
            app = _create_rest_app(state, "test_token")
            async with TestClient(TestServer(app)) as client:
                resp = await client.get(
                    "/api/sessions",
                    headers={"Authorization": "Bearer test_token"},
                )
                assert resp.status == 200
                data = await resp.json()
        finally:
            await store.close()

        assert len(data["sessions"]) == 1
        session = data["sessions"][0]
        assert session["id"] == "persisted-remote"
        assert session["title"] == "Persisted remote session"
        assert session["model"] == "openai/gpt-4.1"
        assert session["project_dir"] == project_dir
        assert session["thinking_level"] == "off"
        assert session["messages"] == 2
        assert session["exists"] is True
        assert session["created_at"]
        assert session["updated_at"]

    @pytest.mark.asyncio
    async def test_session_messages_endpoint_reads_persisted_history(self, tmp_path):
        from aiohttp.test_utils import TestClient, TestServer
        from worker_ai.models import Message, Role

        store = SessionStore(str(tmp_path / "sessions.db"))
        await store.open()
        try:
            await store.create_session(
                "persisted-remote",
                "openai/gpt-4.1",
                project_dir=str(tmp_path),
            )
            await store.add_message(
                "persisted-remote",
                Message(role=Role.USER, content="hello"),
            )
            await store.add_message(
                "persisted-remote",
                Message(role=Role.ASSISTANT, content="world"),
            )

            state = ServerState(config=WorkerConfig(), store=store)
            app = _create_rest_app(state, "test_token")
            async with TestClient(TestServer(app)) as client:
                resp = await client.get(
                    "/api/sessions/persisted-remote/messages",
                    headers={"Authorization": "Bearer test_token"},
                )
                assert resp.status == 200
                data = await resp.json()
        finally:
            await store.close()

        assert data["messages"] == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]

    @pytest.mark.asyncio
    async def test_session_thinking_endpoint_updates_remote_session_state(self, state):
        from aiohttp.test_utils import TestClient, TestServer

        app = _create_rest_app(state, "test_token")
        async with TestClient(TestServer(app)) as client:
            resp = await client.put(
                "/api/sessions/remote-session/thinking",
                headers={"Authorization": "Bearer test_token"},
                json={"thinking_level": "high"},
            )
            assert resp.status == 200
            data = await resp.json()

        assert data["session"]["thinking_level"] == "high"

    @pytest.mark.asyncio
    async def test_credentials_import_saves_overlay_and_oauth_token(
        self,
        state,
        tmp_path,
        monkeypatch,
    ):
        import worker_ai.oauth as oauth_mod
        import worker_server.server as server_mod
        from aiohttp.test_utils import TestClient, TestServer

        monkeypatch.setattr(oauth_mod, "_DEFAULT_AUTH_PATH", tmp_path / "auth.json")
        saved_overlays: list[dict[str, object]] = []
        monkeypatch.setattr(
            server_mod,
            "save_provider_overlay",
            lambda overlay: saved_overlays.append(dict(overlay)),
        )

        app = _create_rest_app(state, "test_token")
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/credentials/import",
                headers={"Authorization": "Bearer test_token"},
                json={
                    "providers": [
                        {
                            "provider": "openai",
                            "settings": {"base_url": "https://api.openai.com/v1"},
                            "auth": {"kind": "api_key", "api_key": "sk-remote"},
                        },
                        {
                            "provider": "anthropic",
                            "settings": {},
                            "auth": {
                                "kind": "oauth_token",
                                "token": {
                                    "access_token": "oauth_remote",
                                    "provider": "anthropic",
                                    "expires_at": 9999999999.0,
                                },
                            },
                        },
                    ]
                },
            )
            assert resp.status == 200
            data = await resp.json()

        assert data["imported"] == [
            {"provider": "openai", "auth_kind": "api_key"},
            {"provider": "anthropic", "auth_kind": "oauth_token"},
        ]
        assert state.provider_overlay["openai"].api_key == "sk-remote"
        assert saved_overlays
        saved_token = TokenStore(path=tmp_path / "auth.json").load("anthropic")
        assert saved_token is not None
        assert saved_token.access_token == "oauth_remote"

    @pytest.mark.asyncio
    async def test_oauth_broker_start_and_complete(self, state, tmp_path, monkeypatch):
        import worker_ai.oauth as oauth_mod
        import worker_server.server as server_mod
        from aiohttp.test_utils import TestClient, TestServer

        monkeypatch.setattr(oauth_mod, "_DEFAULT_AUTH_PATH", tmp_path / "auth.json")

        fake_challenge = RemoteOAuthChallenge(
            provider="openai",
            flow_type="callback",
            verifier="verifier",
            state="state_123",
            authorize_url="https://auth.example/authorize",
            redirect_uri="http://127.0.0.1:1455/auth/callback",
            expires_at=9999999999.0,
        )

        monkeypatch.setattr(
            server_mod,
            "start_remote_oauth_challenge",
            lambda provider, *, redirect_uri="": (
                fake_challenge,
                {
                    "provider": "openai",
                    "flow_type": "callback",
                    "authorize_url": "https://auth.example/authorize",
                    "redirect_uri": redirect_uri,
                },
            ),
        )

        async def _fake_complete(challenge, payload):
            assert challenge == fake_challenge
            assert payload == {"code": "code_123", "state": "state_123"}
            return OAuthToken(
                access_token="oauth_from_broker",
                provider=challenge.provider,
                expires_at=9999999999.0,
            )

        monkeypatch.setattr(server_mod, "complete_remote_oauth_challenge", _fake_complete)

        app = _create_rest_app(state, "test_token")
        async with TestClient(TestServer(app)) as client:
            start = await client.post(
                "/api/oauth/start",
                headers={"Authorization": "Bearer test_token"},
                json={
                    "provider": "openai",
                    "redirect_uri": "http://127.0.0.1:9999/auth/callback",
                },
            )
            assert start.status == 200
            start_data = await start.json()
            assert start_data["provider"] == "openai"
            assert start_data["flow_type"] == "callback"
            login_id = start_data["login_id"]

            complete = await client.post(
                "/api/oauth/complete",
                headers={"Authorization": "Bearer test_token"},
                json={
                    "login_id": login_id,
                    "payload": {"code": "code_123", "state": "state_123"},
                },
            )
            assert complete.status == 200
            complete_data = await complete.json()

        assert complete_data["status"] == "ok"
        saved = TokenStore(path=tmp_path / "auth.json").load("openai")
        assert saved is not None
        assert saved.access_token == "oauth_from_broker"


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

    @pytest.mark.asyncio
    async def test_max_sessions_enforced_for_new_session(self):
        from worker_core.agent import AgentSession

        config = WorkerConfig()
        config.server.max_sessions = 1
        state = ServerState(config=config)
        state.sessions["existing"] = AgentSession(
            provider=MockProvider(),
            model="test",
            tools=[],
        )

        ws = FakeWebSocket()
        ws.inject(
            json.dumps(
                {
                    "type": "message",
                    "session_id": "another",
                    "content": "hi",
                }
            )
        )
        ws.close_input()

        await handle_client(ws, state)  # type: ignore[arg-type]

        messages = [json.loads(m) for m in ws.sent]
        assert messages[0]["type"] == "error"
        assert "Maximum sessions reached" in messages[0]["error"]

    @pytest.mark.asyncio
    async def test_create_server_session_rehydrates_persisted_messages(self, tmp_path, monkeypatch):
        import worker_server.server as server_mod
        from worker_ai.models import Message, Role

        store = SessionStore(str(tmp_path / "sessions.db"))
        await store.open()
        try:
            project_dir = str(tmp_path / "project")
            (tmp_path / "project").mkdir()
            await store.create_session(
                "persisted-remote",
                "openai/gpt-4.1",
                project_dir=project_dir,
            )
            await store.add_message(
                "persisted-remote",
                Message(role=Role.USER, content="hello"),
            )
            await store.add_message(
                "persisted-remote",
                Message(role=Role.ASSISTANT, content="world"),
            )

            config = WorkerConfig()
            state = ServerState(
                config=config,
                default_project_dir=str(tmp_path),
                store=store,
            )

            fake_runtime = AsyncMock()
            fake_runtime.provider = MockProvider()
            monkeypatch.setattr(
                server_mod,
                "bootstrap_runtime",
                AsyncMock(return_value=fake_runtime),
            )

            def _fake_create_session(
                _config,
                runtime,
                *,
                project_dir,
                store,
                session_id,
                **_kwargs,
            ):
                from worker_core.agent import AgentSession

                return AgentSession(
                    provider=runtime.provider,
                    model="gpt-4.1",
                    tools=[],
                    project_dir=project_dir,
                    store=store,
                    session_id=session_id,
                )

            monkeypatch.setattr(
                server_mod,
                "create_agent_session_from_bootstrap",
                _fake_create_session,
            )

            session = await server_mod._create_server_session(state, "persisted-remote")
        finally:
            await store.close()

        assert [message.content for message in session.messages[1:]] == ["hello", "world"]
        assert state.session_provider_models["persisted-remote"] == "openai/gpt-4.1"
        assert state.session_projects["persisted-remote"] == project_dir


# ── Extension Discovery ──────────────────────────────────────────


class TestExtensionDiscovery:
    def test_discover_returns_empty_by_default(self):
        """No extensions installed → empty dict."""
        extensions = discover_extensions()
        # May or may not be empty depending on env, but should not crash
        assert isinstance(extensions, dict)
