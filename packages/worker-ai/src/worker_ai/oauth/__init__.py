"""OAuth authentication for LLM providers.

Supports three OAuth flows (matching OpenCode architecture):
- Device flow (RFC 8628): Kimi
- Browser PKCE with code paste: Anthropic
- Browser PKCE with local callback: OpenAI

Token persistence in ~/.config/worker/auth.json with auto-refresh.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
import urllib.parse
import webbrowser
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("worker.oauth")

# ── Token storage ─────────────────────────────────────────────────

_DEFAULT_AUTH_PATH = Path("~/.config/worker/auth.json").expanduser()


@dataclass
class OAuthToken:
    access_token: str
    refresh_token: str = ""
    token_type: str = "Bearer"
    expires_at: float = 0.0  # Unix timestamp
    scope: str = ""
    provider: str = ""

    @property
    def is_expired(self) -> bool:
        if self.expires_at <= 0:
            return False  # No expiry info → assume valid
        return time.time() >= self.expires_at - 60  # 1 min buffer


class TokenStore:
    """Persist OAuth tokens in a JSON file."""

    def __init__(self, path: Path | None = None):
        self.path = path or _DEFAULT_AUTH_PATH

    def load(self, provider: str) -> OAuthToken | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            entry = data.get(provider)
            if entry:
                return OAuthToken(**entry)
        except (json.JSONDecodeError, TypeError):
            pass
        return None

    def save(self, token: OAuthToken) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        data[token.provider] = asdict(token)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def remove(self, provider: str) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            data.pop(provider, None)
            self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except json.JSONDecodeError:
            pass


# ── PKCE helpers ──────────────────────────────────────────────────


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE verifier and S256 challenge (RFC 7636)."""
    verifier = secrets.token_urlsafe(43)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ── Base OAuth provider ───────────────────────────────────────────


class OAuthProvider(ABC):
    """Base class for provider-specific OAuth flows."""

    name: str

    def __init__(self, token_store: TokenStore | None = None):
        self.store = token_store or TokenStore()

    @abstractmethod
    async def login(self) -> OAuthToken:
        """Run the OAuth flow and return a token."""
        ...

    @abstractmethod
    async def refresh(self, token: OAuthToken) -> OAuthToken:
        """Refresh an expired token."""
        ...

    async def get_token(self) -> OAuthToken | None:
        """Get a valid token, refreshing if needed."""
        token = self.store.load(self.name)
        if token is None:
            return None
        if token.is_expired and token.refresh_token:
            try:
                token = await self.refresh(token)
                self.store.save(token)
            except Exception:
                logger.warning("Token refresh failed for %s", self.name)
                return None
        return token


# ── Device Authorization Flow (RFC 8628) ─────────────────────────


class _DeviceFlowOAuth(OAuthProvider):
    """Device Authorization Grant (RFC 8628).

    1. POST device_authorization_url → user_code, verification_uri, device_code
    2. User visits verification_uri and enters user_code
    3. Poll token_url until authorized
    """

    DEVICE_AUTH_URL: str
    TOKEN_URL: str
    CLIENT_ID: str = "worker-agent"
    SCOPE: str = ""

    async def login(self) -> OAuthToken:
        async with httpx.AsyncClient() as client:
            payload: dict[str, str] = {"client_id": self.CLIENT_ID}
            if self.SCOPE:
                payload["scope"] = self.SCOPE

            resp = await client.post(self.DEVICE_AUTH_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()

            device_code = data["device_code"]
            user_code = data["user_code"]
            verification_uri = (
                data.get("verification_uri") or data.get("verification_url", "")
            )
            interval = data.get("interval", 5)
            expires_in = data.get("expires_in", 900)

            print(f"\n  {self.name.capitalize()} OAuth — Device Authorization")
            print(f"   Open:  {verification_uri}")
            print(f"   Code:  {user_code}")
            print(f"   Waiting for authorization...\n")

            try:
                webbrowser.open(verification_uri)
            except Exception:
                pass

            deadline = time.time() + expires_in
            while time.time() < deadline:
                await asyncio.sleep(interval)
                token_resp = await client.post(
                    self.TOKEN_URL,
                    json={
                        "client_id": self.CLIENT_ID,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                )
                if token_resp.status_code == 200:
                    token_data = token_resp.json()
                    token = OAuthToken(
                        access_token=token_data["access_token"],
                        refresh_token=token_data.get("refresh_token", ""),
                        token_type=token_data.get("token_type", "Bearer"),
                        expires_at=time.time()
                        + token_data.get("expires_in", 3600),
                        provider=self.name,
                    )
                    self.store.save(token)
                    print(f"  {self.name.capitalize()} authorized!")
                    return token

                error = token_resp.json().get("error", "")
                if error == "authorization_pending":
                    continue
                if error == "slow_down":
                    interval += 5
                    continue
                raise RuntimeError(
                    f"{self.name.capitalize()} OAuth failed: {error}"
                )

            raise TimeoutError(
                f"{self.name.capitalize()} device authorization timed out."
            )

    async def refresh(self, token: OAuthToken) -> OAuthToken:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.TOKEN_URL,
                json={
                    "client_id": self.CLIENT_ID,
                    "grant_type": "refresh_token",
                    "refresh_token": token.refresh_token,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return OAuthToken(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token", token.refresh_token),
                token_type=data.get("token_type", "Bearer"),
                expires_at=time.time() + data.get("expires_in", 3600),
                provider=self.name,
            )


# ── Browser PKCE OAuth with code paste ────────────────────────────


class _CodePasteOAuth(OAuthProvider):
    """Browser OAuth with PKCE — user copies code from redirect page.

    Flow:
    1. Open browser → provider authorize URL (with PKCE challenge)
    2. User authorizes → provider shows a code on its own page
    3. User pastes code back into terminal
    4. Exchange code + verifier for tokens
    """

    AUTH_URL: str
    TOKEN_URL: str
    CLIENT_ID: str
    REDIRECT_URI: str
    SCOPE: str

    async def login(self) -> OAuthToken:
        verifier, challenge = _generate_pkce()
        state = secrets.token_urlsafe(32)

        params = urllib.parse.urlencode(
            {
                "client_id": self.CLIENT_ID,
                "redirect_uri": self.REDIRECT_URI,
                "response_type": "code",
                "scope": self.SCOPE,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
            }
        )
        auth_url = f"{self.AUTH_URL}?{params}"

        print(f"\n  {self.name.capitalize()} OAuth — Browser Authorization")
        print(f"   Opening browser...")
        print(f"   If it doesn't open, visit:\n   {auth_url}\n")

        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

        # User pastes the code from the browser (blocking I/O in executor)
        code_input: str = await asyncio.get_event_loop().run_in_executor(
            None, lambda: input("  Paste the authorization code: ").strip()
        )

        # Code may contain #state suffix (Anthropic convention)
        parts = code_input.split("#")
        code = parts[0]
        received_state = parts[1] if len(parts) > 1 else state

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.TOKEN_URL,
                headers={"Content-Type": "application/json"},
                json={
                    "code": code,
                    "state": received_state,
                    "grant_type": "authorization_code",
                    "client_id": self.CLIENT_ID,
                    "redirect_uri": self.REDIRECT_URI,
                    "code_verifier": verifier,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        token = OAuthToken(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
            token_type=data.get("token_type", "Bearer"),
            expires_at=time.time() + data.get("expires_in", 3600),
            provider=self.name,
        )
        self.store.save(token)
        print(f"  {self.name.capitalize()} authorized!")
        return token

    async def refresh(self, token: OAuthToken) -> OAuthToken:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.TOKEN_URL,
                headers={"Content-Type": "application/json"},
                json={
                    "grant_type": "refresh_token",
                    "refresh_token": token.refresh_token,
                    "client_id": self.CLIENT_ID,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return OAuthToken(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token", token.refresh_token),
                token_type=data.get("token_type", "Bearer"),
                expires_at=time.time() + data.get("expires_in", 3600),
                provider=self.name,
            )


# ── Browser PKCE OAuth with local callback ────────────────────────


class _LocalCallbackOAuth(OAuthProvider):
    """Browser OAuth with PKCE — local HTTP server captures the callback.

    Flow:
    1. Start temporary HTTP server on localhost
    2. Open browser → provider authorize URL (with PKCE challenge)
    3. User authorizes → browser redirects to localhost
    4. Server captures code, validates state, responds with success page
    5. Exchange code + verifier for tokens
    """

    AUTH_URL: str
    TOKEN_URL: str
    CLIENT_ID: str
    REDIRECT_PORT: int
    REDIRECT_PATH: str = "/auth/callback"
    SCOPE: str
    EXTRA_PARAMS: dict[str, str] = {}

    @property
    def _redirect_uri(self) -> str:
        return f"http://localhost:{self.REDIRECT_PORT}{self.REDIRECT_PATH}"

    async def login(self) -> OAuthToken:
        from aiohttp import web

        verifier, challenge = _generate_pkce()
        state = secrets.token_urlsafe(32)

        params: dict[str, str] = {
            "response_type": "code",
            "client_id": self.CLIENT_ID,
            "redirect_uri": self._redirect_uri,
            "scope": self.SCOPE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        params.update(self.EXTRA_PARAMS)
        auth_url = f"{self.AUTH_URL}?{urllib.parse.urlencode(params)}"

        code_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

        _SUCCESS_HTML = (
            "<html><body><h1>Authorized!</h1>"
            "<p>You can close this tab and return to the terminal.</p>"
            "<script>setTimeout(()=>window.close(),2000)</script>"
            "</body></html>"
        )

        async def handle_callback(request: web.Request) -> web.Response:
            error = request.query.get("error")
            if error:
                desc = request.query.get("error_description", error)
                if not code_future.done():
                    code_future.set_exception(
                        RuntimeError(f"OAuth error: {desc}")
                    )
                return web.Response(
                    text=f"<html><body><h1>Failed</h1><p>{desc}</p></body></html>",
                    content_type="text/html",
                    status=400,
                )

            received_state = request.query.get("state", "")
            if received_state != state:
                if not code_future.done():
                    code_future.set_exception(
                        RuntimeError("Invalid state — possible CSRF")
                    )
                return web.Response(text="Invalid state", status=400)

            code = request.query.get("code", "")
            if code and not code_future.done():
                code_future.set_result(code)
                return web.Response(
                    text=_SUCCESS_HTML, content_type="text/html"
                )

            if not code_future.done():
                code_future.set_exception(
                    RuntimeError("Missing authorization code")
                )
            return web.Response(text="Missing code", status=400)

        app = web.Application()
        app.router.add_get(self.REDIRECT_PATH, handle_callback)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", self.REDIRECT_PORT)
        await site.start()

        print(f"\n  {self.name.capitalize()} OAuth — Browser Authorization")
        print(f"   Opening browser...")
        print(f"   Waiting for authorization...\n")

        try:
            webbrowser.open(auth_url)
        except Exception:
            print(f"   Open manually:\n   {auth_url}")

        try:
            code = await asyncio.wait_for(code_future, timeout=300)
        finally:
            await runner.cleanup()

        # Exchange code for tokens (x-www-form-urlencoded per OpenAI spec)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                content=urllib.parse.urlencode(
                    {
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": self._redirect_uri,
                        "client_id": self.CLIENT_ID,
                        "code_verifier": verifier,
                    }
                ),
            )
            resp.raise_for_status()
            data = resp.json()

        token = OAuthToken(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
            token_type=data.get("token_type", "Bearer"),
            expires_at=time.time() + data.get("expires_in", 3600),
            provider=self.name,
        )
        self.store.save(token)
        print(f"  {self.name.capitalize()} authorized!")
        return token

    async def refresh(self, token: OAuthToken) -> OAuthToken:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                content=urllib.parse.urlencode(
                    {
                        "grant_type": "refresh_token",
                        "refresh_token": token.refresh_token,
                        "client_id": self.CLIENT_ID,
                    }
                ),
            )
            resp.raise_for_status()
            data = resp.json()
            return OAuthToken(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token", token.refresh_token),
                token_type=data.get("token_type", "Bearer"),
                expires_at=time.time() + data.get("expires_in", 3600),
                provider=self.name,
            )


# ── Provider-specific configs ─────────────────────────────────────


class KimiOAuth(_DeviceFlowOAuth):
    name = "kimi"
    DEVICE_AUTH_URL = "https://auth.kimi.com/api/oauth/device_authorization"
    TOKEN_URL = "https://auth.kimi.com/api/oauth/token"
    SCOPE = "coding"


class AnthropicOAuth(_CodePasteOAuth):
    """Anthropic OAuth — browser PKCE with code paste (Claude Pro/Max).

    Browser opens claude.ai/oauth/authorize, user authorizes,
    Anthropic shows a code on its callback page, user pastes it back.

    Uses claude.ai domain (not console.anthropic.com) to get inference
    access via Claude Pro/Max subscription.
    """

    name = "anthropic"
    AUTH_URL = "https://claude.ai/oauth/authorize"
    TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
    CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
    SCOPE = "user:profile user:inference user:sessions:claude_code"


class OpenAIOAuth(_LocalCallbackOAuth):
    """OpenAI Codex OAuth — browser PKCE with local callback.

    Browser opens auth.openai.com, user authorizes, browser redirects
    to localhost where a temporary server captures the authorization code.
    """

    name = "openai"
    AUTH_URL = "https://auth.openai.com/oauth/authorize"
    TOKEN_URL = "https://auth.openai.com/oauth/token"
    CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
    REDIRECT_PORT = 1455
    REDIRECT_PATH = "/auth/callback"
    SCOPE = "openid profile email offline_access"
    EXTRA_PARAMS = {
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": "worker",
    }


# ── Registry ──────────────────────────────────────────────────────

OAUTH_PROVIDERS: dict[str, type[OAuthProvider]] = {
    "kimi": KimiOAuth,
    "anthropic": AnthropicOAuth,
    "openai": OpenAIOAuth,
}


def get_oauth_provider(name: str) -> OAuthProvider | None:
    """Get an OAuth provider by name, or None if not supported."""
    cls = OAUTH_PROVIDERS.get(name)
    return cls() if cls else None
