"""Tests for GitHub Copilot OAuth broker support."""

from __future__ import annotations

import pytest
from artel_ai.oauth import (
    GitHubCopilotEnterpriseOAuth,
    GitHubCopilotOAuth,
    TokenStore,
    get_oauth_provider,
)
from artel_core.config import ArtelConfig, ProviderConfig


class TestGitHubCopilotOAuthProviderResolution:
    def test_get_oauth_provider_accepts_github_copilot_alias(self):
        provider = get_oauth_provider("github-copilot")

        assert isinstance(provider, GitHubCopilotOAuth)
        assert provider.name == "github_copilot"

    def test_get_oauth_provider_reads_enterprise_host_from_config(self):
        config = ArtelConfig(
            providers={
                "github_copilot_enterprise": ProviderConfig(
                    options={"github_host": "octo.ghe.com"},
                )
            }
        )

        provider = get_oauth_provider("github-copilot-enterprise", config=config)

        assert isinstance(provider, GitHubCopilotEnterpriseOAuth)
        assert provider.name == "github_copilot_enterprise"
        assert provider.github_host == "octo.ghe.com"


class TestGitHubCopilotOAuthBroker:
    @pytest.mark.asyncio
    async def test_login_runs_gh_auth_and_persists_token(self, tmp_path, monkeypatch):
        import artel_ai.oauth as oauth_mod

        seen_commands: list[list[str]] = []

        async def fake_run_command(args: list[str]) -> int:
            seen_commands.append(args)
            return 0

        async def fake_load_token(github_host: str) -> str | None:
            assert github_host == "octo.ghe.com"
            return "gho_login_token"

        monkeypatch.setattr(oauth_mod, "_run_command", fake_run_command)
        monkeypatch.setattr(
            oauth_mod,
            "load_github_copilot_token_from_gh_cli",
            fake_load_token,
        )

        provider = GitHubCopilotEnterpriseOAuth(
            token_store=TokenStore(path=tmp_path / "auth.json"),
            github_host="octo.ghe.com",
        )

        token = await provider.login()

        assert token.access_token == "gho_login_token"
        assert token.provider == "github_copilot_enterprise"
        assert seen_commands == [
            [
                "gh",
                "auth",
                "login",
                "--web",
                "--clipboard",
                "--skip-ssh-key",
                "--hostname",
                "octo.ghe.com",
            ]
        ]
        saved = TokenStore(path=tmp_path / "auth.json").load("github_copilot_enterprise")
        assert saved is not None
        assert saved.access_token == "gho_login_token"

    @pytest.mark.asyncio
    async def test_refresh_reloads_token_from_gh_cli(self, tmp_path, monkeypatch):
        import artel_ai.oauth as oauth_mod
        from artel_ai.oauth import OAuthToken

        async def fake_load_token(github_host: str) -> str | None:
            assert github_host == "octo.ghe.com"
            return "gho_refreshed_token"

        monkeypatch.setattr(
            oauth_mod,
            "load_github_copilot_token_from_gh_cli",
            fake_load_token,
        )
        monkeypatch.setattr(
            oauth_mod,
            "load_github_copilot_token_from_files",
            lambda github_host: None,
        )

        provider = GitHubCopilotEnterpriseOAuth(
            token_store=TokenStore(path=tmp_path / "auth.json"),
            github_host="octo.ghe.com",
        )

        refreshed = await provider.refresh(
            OAuthToken(
                access_token="expired",
                token_type="Bearer",
                provider="github_copilot_enterprise",
                expires_at=1.0,
            )
        )

        assert refreshed.access_token == "gho_refreshed_token"
        assert refreshed.provider == "github_copilot_enterprise"
