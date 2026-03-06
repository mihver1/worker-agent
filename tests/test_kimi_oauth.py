"""Tests for Kimi OAuth device-flow behavior."""

from __future__ import annotations

import urllib.parse

import pytest


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object]):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, object]:
        return self._payload


class _FakeAsyncClient:
    def __init__(
        self,
        responses: list[_FakeResponse],
        captured_posts: list[tuple[str, dict[str, object]]],
    ) -> None:
        self._responses = responses
        self._captured_posts = captured_posts

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def post(self, url: str, **kwargs: object) -> _FakeResponse:
        self._captured_posts.append((url, kwargs))
        return self._responses.pop(0)


def _parse_form_content(content: object) -> dict[str, list[str]]:
    assert isinstance(content, str)
    return urllib.parse.parse_qs(content, keep_blank_values=True)


class TestKimiOAuth:
    @pytest.mark.asyncio
    async def test_login_uses_upstream_client_id_and_form_encoded_requests(
        self,
        tmp_path,
        monkeypatch,
    ):
        import worker_ai.oauth as oauth_mod

        captured_posts: list[tuple[str, dict[str, object]]] = []
        responses = [
            _FakeResponse(
                200,
                {
                    "device_code": "device-123",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://auth.kimi.com/device",
                    "interval": 0,
                    "expires_in": 60,
                },
            ),
            _FakeResponse(
                200,
                {
                    "access_token": "access-123",
                    "refresh_token": "refresh-123",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            ),
        ]

        monkeypatch.setattr(
            oauth_mod.httpx,
            "AsyncClient",
            lambda: _FakeAsyncClient(responses, captured_posts),
        )
        opened_urls: list[str] = []
        monkeypatch.setattr(
            oauth_mod.webbrowser,
            "open",
            lambda url: opened_urls.append(url) or True,
        )

        async def _no_sleep(_: float) -> None:
            return None

        monkeypatch.setattr(oauth_mod.asyncio, "sleep", _no_sleep)

        provider = oauth_mod.KimiOAuth(token_store=oauth_mod.TokenStore(tmp_path / "auth.json"))
        token = await provider.login()

        assert token.access_token == "access-123"
        assert token.refresh_token == "refresh-123"
        assert len(captured_posts) == 2
        assert opened_urls == ["https://auth.kimi.com/device?user_code=ABCD-EFGH"]

        auth_url, auth_kwargs = captured_posts[0]
        assert auth_url == "https://auth.kimi.com/api/oauth/device_authorization"
        assert auth_kwargs["headers"] == {"Content-Type": "application/x-www-form-urlencoded"}
        auth_form = _parse_form_content(auth_kwargs["content"])
        assert auth_form == {
            "client_id": ["17e5f671-d194-4dfb-9706-5516cb48c098"],
        }

        token_url, token_kwargs = captured_posts[1]
        assert token_url == "https://auth.kimi.com/api/oauth/token"
        token_form = _parse_form_content(token_kwargs["content"])
        assert token_form == {
            "client_id": ["17e5f671-d194-4dfb-9706-5516cb48c098"],
            "device_code": ["device-123"],
            "grant_type": ["urn:ietf:params:oauth:grant-type:device_code"],
        }

    @pytest.mark.asyncio
    async def test_login_prefers_verification_uri_complete_when_present(
        self,
        tmp_path,
        monkeypatch,
    ):
        import worker_ai.oauth as oauth_mod

        captured_posts: list[tuple[str, dict[str, object]]] = []
        responses = [
            _FakeResponse(
                200,
                {
                    "device_code": "device-123",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://www.kimi.com/code/authorize_device",
                    "verification_uri_complete": (
                        "https://www.kimi.com/code/authorize_device?user_code=ABCD-EFGH"
                    ),
                    "interval": 0,
                    "expires_in": 60,
                },
            ),
            _FakeResponse(
                200,
                {
                    "access_token": "access-123",
                    "refresh_token": "refresh-123",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            ),
        ]

        monkeypatch.setattr(
            oauth_mod.httpx,
            "AsyncClient",
            lambda: _FakeAsyncClient(responses, captured_posts),
        )
        opened_urls: list[str] = []
        monkeypatch.setattr(
            oauth_mod.webbrowser,
            "open",
            lambda url: opened_urls.append(url) or True,
        )

        async def _no_sleep(_: float) -> None:
            return None

        monkeypatch.setattr(oauth_mod.asyncio, "sleep", _no_sleep)

        provider = oauth_mod.KimiOAuth(
            token_store=oauth_mod.TokenStore(tmp_path / "auth.json")
        )
        await provider.login()

        assert opened_urls == [
            "https://www.kimi.com/code/authorize_device?user_code=ABCD-EFGH"
        ]

    @pytest.mark.asyncio
    async def test_refresh_uses_form_encoded_request(self, monkeypatch):
        import worker_ai.oauth as oauth_mod

        captured_posts: list[tuple[str, dict[str, object]]] = []
        responses = [
            _FakeResponse(
                200,
                {
                    "access_token": "refreshed-123",
                    "refresh_token": "refresh-456",
                    "token_type": "Bearer",
                    "expires_in": 7200,
                },
            ),
        ]

        monkeypatch.setattr(
            oauth_mod.httpx,
            "AsyncClient",
            lambda: _FakeAsyncClient(responses, captured_posts),
        )

        provider = oauth_mod.KimiOAuth()
        token = await provider.refresh(
            oauth_mod.OAuthToken(
                access_token="expired",
                refresh_token="refresh-123",
                provider="kimi",
            )
        )

        assert token.access_token == "refreshed-123"
        assert token.refresh_token == "refresh-456"

        refresh_url, refresh_kwargs = captured_posts[0]
        assert refresh_url == "https://auth.kimi.com/api/oauth/token"
        assert refresh_kwargs["headers"] == {"Content-Type": "application/x-www-form-urlencoded"}
        refresh_form = _parse_form_content(refresh_kwargs["content"])
        assert refresh_form == {
            "client_id": ["17e5f671-d194-4dfb-9706-5516cb48c098"],
            "grant_type": ["refresh_token"],
            "refresh_token": ["refresh-123"],
        }
