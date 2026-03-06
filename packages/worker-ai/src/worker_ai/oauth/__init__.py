"""OAuth authentication for LLM providers.

Supports browser/OAuth flows for providers that expose them in Worker:
- Browser PKCE with code paste: Anthropic
- Browser PKCE with local callback: OpenAI
- GitHub CLI broker flow: GitHub Copilot

Token persistence in ~/.config/worker/auth.json with auto-refresh.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
import urllib.parse
import webbrowser
from abc import ABC, abstractmethod
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from worker_ai.provider_specs import get_provider_spec

logger = logging.getLogger("worker.oauth")
_GITHUB_COPILOT_PROVIDER_IDS = frozenset({"github_copilot", "github_copilot_enterprise"})

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
    account_id: str = ""  # e.g. chatgpt_account_id for OpenAI

    @property
    def is_expired(self) -> bool:
        if self.expires_at <= 0:
            return False  # No expiry info → assume valid
        return time.time() >= self.expires_at - 60  # 1 min buffer


@dataclass(frozen=True)
class RemoteOAuthChallenge:
    """Provider-specific OAuth challenge data for remote broker flows."""

    provider: str
    flow_type: str
    verifier: str
    state: str
    authorize_url: str
    redirect_uri: str = ""
    expires_at: float = 0.0


def parse_jwt_claims(token: str) -> dict[str, Any]:
    """Decode JWT payload without verification (for extracting claims only)."""
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        payload = parts[1]
        # Add padding
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        data = base64.urlsafe_b64decode(payload)
        return json.loads(data)
    except Exception:
        return {}


def extract_openai_account_id(claims: dict[str, Any]) -> str:
    """Extract chatgpt_account_id from JWT claims (access_token or id_token)."""
    return (
        claims.get("chatgpt_account_id")
        or (claims.get("https://api.openai.com/auth") or {}).get("chatgpt_account_id")
        or (claims.get("organizations", [{}])[0].get("id") if claims.get("organizations") else "")
        or ""
    )


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
            with suppress(json.JSONDecodeError):
                data = json.loads(self.path.read_text(encoding="utf-8"))
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

def _resolve_provider_id(name: str) -> str:
    spec = get_provider_spec(name)
    if spec is not None:
        return spec.id
    return name


def _get_provider_options(config: Any | None, provider_name: str) -> dict[str, Any]:
    if config is None:
        return {}
    providers = getattr(config, "providers", None)
    if not isinstance(providers, dict):
        return {}

    provider_config = providers.get(provider_name)
    if provider_config is None:
        resolved_name = _resolve_provider_id(provider_name)
        if resolved_name != provider_name:
            provider_config = providers.get(resolved_name)
    if provider_config is None:
        return {}

    options = getattr(provider_config, "options", None)
    return dict(options) if isinstance(options, dict) else {}


def normalize_github_host(raw_host: str) -> str:
    host = raw_host.strip()
    if not host:
        return ""
    host = host.removeprefix("https://").removeprefix("http://")
    return host.split("/", 1)[0].lower()


def is_github_copilot_provider(provider_name: str) -> bool:
    return _resolve_provider_id(provider_name) in _GITHUB_COPILOT_PROVIDER_IDS


def get_github_copilot_host(config: Any | None, provider_name: str) -> str:
    options = _get_provider_options(config, provider_name)
    host = str(options.get("github_host") or options.get("hostname") or "")
    if not host:
        host = os.environ.get("GH_HOST", "")
    return normalize_github_host(host)


def _looks_like_github_token(key: str, value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    lowered_key = key.lower()
    return (
        "token" in lowered_key
        or "oauth" in lowered_key
        or candidate.startswith(("gho_", "ghp_", "ghu_", "ghs_", "ghr_", "github_pat_"))
    )


def _extract_github_copilot_token(payload: Any, *, github_host: str = "") -> str | None:
    if isinstance(payload, dict):
        if github_host:
            for key, value in payload.items():
                if normalize_github_host(str(key)) == github_host:
                    token = _extract_github_copilot_token(value, github_host=github_host)
                    if token:
                        return token
        for key, value in payload.items():
            if isinstance(value, str) and _looks_like_github_token(str(key), value):
                return value.strip()
        for value in payload.values():
            token = _extract_github_copilot_token(value, github_host=github_host)
            if token:
                return token
        return None
    if isinstance(payload, list):
        for value in payload:
            token = _extract_github_copilot_token(value, github_host=github_host)
            if token:
                return token
        return None
    if isinstance(payload, str) and _looks_like_github_token("", payload):
        return payload.strip()
    return None


def _github_copilot_token_paths() -> tuple[Path, ...]:
    home = Path.home()
    return (
        home / ".copilot" / "config.json",
        home / ".config" / "github-copilot" / "hosts.json",
        home / ".config" / "github-copilot" / "apps.json",
    )


def load_github_copilot_token_from_files(github_host: str) -> str | None:
    for path in _github_copilot_token_paths():
        try:
            payload = json.loads(path.read_text())
        except (FileNotFoundError, IsADirectoryError, OSError, json.JSONDecodeError):
            continue
        token = _extract_github_copilot_token(payload, github_host=github_host)
        if token:
            return token
    return None


async def load_github_copilot_token_from_gh_cli(github_host: str) -> str | None:
    args = ["gh", "auth", "token"]
    if github_host:
        args.extend(["--hostname", github_host])
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None

    stdout, _ = await process.communicate()
    if process.returncode != 0:
        return None
    token = stdout.decode().strip()
    return token or None


async def resolve_github_copilot_token(config: Any | None, provider_name: str) -> str | None:
    if not is_github_copilot_provider(provider_name):
        return None
    github_host = get_github_copilot_host(config, provider_name)
    token = load_github_copilot_token_from_files(github_host)
    if token:
        return token
    return await load_github_copilot_token_from_gh_cli(github_host)


async def _run_command(args: list[str]) -> int:
    try:
        process = await asyncio.create_subprocess_exec(*args)
    except OSError as exc:
        if args and args[0] == "gh":
            raise RuntimeError(
                "GitHub CLI (`gh`) is required for GitHub Copilot login. "
                "Install it first (for example on macOS with Homebrew: `brew install gh`) "
                "or use `GH_TOKEN` / `GITHUB_TOKEN`."
            ) from exc
        raise RuntimeError(f"Required command not found: {args[0]!r}") from exc
    return await process.wait()


# ── PKCE helpers ──────────────────────────────────────────────────


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE verifier and S256 challenge (RFC 7636)."""
    verifier = secrets.token_urlsafe(43)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def start_remote_oauth_challenge(
    provider_name: str,
    *,
    redirect_uri: str = "",
) -> tuple[RemoteOAuthChallenge, dict[str, Any]]:
    """Create an OAuth broker challenge for remote/connect mode."""

    resolved_name = _resolve_provider_id(provider_name)
    verifier, challenge = _generate_pkce()
    state = secrets.token_urlsafe(32)

    if resolved_name == "openai":
        if not redirect_uri:
            raise RuntimeError("OpenAI remote OAuth requires a redirect_uri.")
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": OpenAIOAuth.CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": OpenAIOAuth.SCOPE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        params.update(OpenAIOAuth.EXTRA_PARAMS)
        authorize_url = (
            f"{OpenAIOAuth.AUTH_URL}?{urllib.parse.urlencode(params)}"
        )
        challenge_data = RemoteOAuthChallenge(
            provider=resolved_name,
            flow_type="callback",
            verifier=verifier,
            state=state,
            authorize_url=authorize_url,
            redirect_uri=redirect_uri,
            expires_at=time.time() + 300,
        )
        return challenge_data, {
            "provider": resolved_name,
            "flow_type": "callback",
            "authorize_url": authorize_url,
            "redirect_uri": redirect_uri,
        }

    if resolved_name == "anthropic":
        params = urllib.parse.urlencode(
            {
                "client_id": AnthropicOAuth.CLIENT_ID,
                "redirect_uri": AnthropicOAuth.REDIRECT_URI,
                "response_type": "code",
                "scope": AnthropicOAuth.SCOPE,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
            }
        )
        authorize_url = f"{AnthropicOAuth.AUTH_URL}?{params}"
        challenge_data = RemoteOAuthChallenge(
            provider=resolved_name,
            flow_type="code_paste",
            verifier=verifier,
            state=state,
            authorize_url=authorize_url,
            redirect_uri=AnthropicOAuth.REDIRECT_URI,
            expires_at=time.time() + 300,
        )
        return challenge_data, {
            "provider": resolved_name,
            "flow_type": "code_paste",
            "authorize_url": authorize_url,
        }

    raise RuntimeError(
        f"Remote OAuth broker is not supported for '{provider_name}'."
    )


async def complete_remote_oauth_challenge(
    challenge: RemoteOAuthChallenge,
    payload: dict[str, Any],
) -> OAuthToken:
    """Finish a remote OAuth challenge and return the resulting token."""

    if challenge.expires_at > 0 and time.time() > challenge.expires_at:
        raise TimeoutError("OAuth challenge expired. Start /connect again.")

    if challenge.provider == "openai":
        code = str(payload.get("code", "")).strip()
        received_state = str(payload.get("state", "")).strip()
        if not code:
            raise RuntimeError("Missing authorization code.")
        if received_state != challenge.state:
            raise RuntimeError("Invalid state — possible CSRF")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                OpenAIOAuth.TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                content=urllib.parse.urlencode(
                    {
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": challenge.redirect_uri,
                        "client_id": OpenAIOAuth.CLIENT_ID,
                        "code_verifier": challenge.verifier,
                    }
                ),
            )
            resp.raise_for_status()
            data = resp.json()

        account_id = ""
        id_token = data.get("id_token", "")
        if id_token:
            account_id = extract_openai_account_id(parse_jwt_claims(id_token))
        if not account_id:
            account_id = extract_openai_account_id(
                parse_jwt_claims(data["access_token"])
            )
        return OAuthToken(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
            token_type=data.get("token_type", "Bearer"),
            expires_at=time.time() + data.get("expires_in", 3600),
            provider=challenge.provider,
            account_id=account_id,
        )

    if challenge.provider == "anthropic":
        code_input = str(payload.get("code", "")).strip()
        if not code_input:
            raise RuntimeError("Missing authorization code.")
        parts = code_input.split("#")
        code = parts[0]
        received_state = str(payload.get("state", "")).strip() or (
            parts[1] if len(parts) > 1 else challenge.state
        )

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                AnthropicOAuth.TOKEN_URL,
                headers={"Content-Type": "application/json"},
                json={
                    "code": code,
                    "state": received_state,
                    "grant_type": "authorization_code",
                    "client_id": AnthropicOAuth.CLIENT_ID,
                    "redirect_uri": challenge.redirect_uri,
                    "code_verifier": challenge.verifier,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        return OAuthToken(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
            token_type=data.get("token_type", "Bearer"),
            expires_at=time.time() + data.get("expires_in", 3600),
            provider=challenge.provider,
        )

    raise RuntimeError(
        f"Remote OAuth broker is not supported for '{challenge.provider}'."
    )


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
        if token.is_expired:
            if not token.refresh_token:
                logger.warning(
                    "Expired token without refresh token for %s", self.name
                )
                return None
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

    def _device_flow_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/x-www-form-urlencoded"}

    def _device_authorization_payload(self) -> dict[str, str]:
        payload: dict[str, str] = {"client_id": self.CLIENT_ID}
        if self.SCOPE:
            payload["scope"] = self.SCOPE
        return payload

    def _device_token_payload(self, device_code: str) -> dict[str, str]:
        return {
            "client_id": self.CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }

    def _refresh_payload(self, refresh_token: str) -> dict[str, str]:
        return {
            "client_id": self.CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

    def _verification_url(
        self,
        verification_uri: str,
        user_code: str,
        verification_uri_complete: str = "",
    ) -> str:
        if verification_uri_complete:
            return verification_uri_complete
        if not verification_uri:
            return ""
        parsed = urllib.parse.urlsplit(verification_uri)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        query.setdefault("user_code", [user_code])
        return urllib.parse.urlunsplit(
            parsed._replace(query=urllib.parse.urlencode(query, doseq=True))
        )

    async def login(self) -> OAuthToken:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.DEVICE_AUTH_URL,
                headers=self._device_flow_headers(),
                content=urllib.parse.urlencode(self._device_authorization_payload()),
            )
            resp.raise_for_status()
            data = resp.json()

            device_code = data["device_code"]
            user_code = data["user_code"]
            verification_uri = (
                data.get("verification_uri") or data.get("verification_url", "")
            )
            verification_url = self._verification_url(
                str(verification_uri or ""),
                str(user_code),
                str(data.get("verification_uri_complete") or ""),
            )
            interval = data.get("interval", 5)
            expires_in = data.get("expires_in", 900)

            print(f"\n  {self.name.capitalize()} OAuth — Device Authorization")
            print(f"   Open:  {verification_url or verification_uri}")
            print(f"   Code:  {user_code}")
            print("   Waiting for authorization...\n")
            with suppress(Exception):
                webbrowser.open(verification_url or verification_uri)

            deadline = time.time() + expires_in
            while time.time() < deadline:
                await asyncio.sleep(interval)
                token_resp = await client.post(
                    self.TOKEN_URL,
                    headers=self._device_flow_headers(),
                    content=urllib.parse.urlencode(
                        self._device_token_payload(device_code)
                    ),
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
                headers=self._device_flow_headers(),
                content=urllib.parse.urlencode(self._refresh_payload(token.refresh_token)),
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
        print("   Opening browser...")
        print(f"   If it doesn't open, visit:\n   {auth_url}\n")
        with suppress(Exception):
            webbrowser.open(auth_url)

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
        success_html = (
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
                return web.Response(text=success_html, content_type="text/html")

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
        print("   Opening browser...")
        print("   Waiting for authorization...\n")

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

        # Extract account_id from JWT
        account_id = ""
        id_token = data.get("id_token", "")
        if id_token:
            account_id = extract_openai_account_id(parse_jwt_claims(id_token))
        if not account_id:
            account_id = extract_openai_account_id(parse_jwt_claims(data["access_token"]))

        token = OAuthToken(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
            token_type=data.get("token_type", "Bearer"),
            expires_at=time.time() + data.get("expires_in", 3600),
            provider=self.name,
            account_id=account_id,
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

            # Preserve or re-extract account_id
            account_id = token.account_id
            id_token_str = data.get("id_token", "")
            if id_token_str:
                new_id = extract_openai_account_id(parse_jwt_claims(id_token_str))
                if new_id:
                    account_id = new_id

            return OAuthToken(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token", token.refresh_token),
                token_type=data.get("token_type", "Bearer"),
                expires_at=time.time() + data.get("expires_in", 3600),
                provider=self.name,
                account_id=account_id,
            )


# ── Provider-specific configs ─────────────────────────────────────


class KimiOAuth(_DeviceFlowOAuth):
    name = "kimi"
    DEVICE_AUTH_URL = "https://auth.kimi.com/api/oauth/device_authorization"
    TOKEN_URL = "https://auth.kimi.com/api/oauth/token"
    CLIENT_ID = "17e5f671-d194-4dfb-9706-5516cb48c098"


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

class _GitHubCliOAuth(OAuthProvider):
    """GitHub CLI-backed auth for GitHub Copilot providers."""

    display_name = "GitHub Copilot"

    def __init__(
        self,
        token_store: TokenStore | None = None,
        *,
        github_host: str = "",
    ):
        super().__init__(token_store=token_store)
        self.github_host = normalize_github_host(github_host)

    def _gh_login_args(self) -> list[str]:
        args = ["gh", "auth", "login", "--web", "--clipboard", "--skip-ssh-key"]
        if self.github_host:
            args.extend(["--hostname", self.github_host])
        return args

    async def login(self) -> OAuthToken:
        print(f"\n  {self.display_name} OAuth — GitHub CLI broker")
        print("   Starting GitHub CLI login...\n")

        return_code = await _run_command(self._gh_login_args())
        if return_code != 0:
            raise RuntimeError("GitHub CLI login failed.")

        token_value = await load_github_copilot_token_from_gh_cli(self.github_host)
        if not token_value:
            token_value = load_github_copilot_token_from_files(self.github_host)
        if not token_value:
            raise RuntimeError("GitHub CLI login completed but no GitHub token was found.")

        token = OAuthToken(
            access_token=token_value,
            token_type="Bearer",
            provider=self.name,
        )
        self.store.save(token)
        print(f"  {self.display_name} authorized!")
        return token

    async def refresh(self, token: OAuthToken) -> OAuthToken:
        token_value = load_github_copilot_token_from_files(self.github_host)
        if not token_value:
            token_value = await load_github_copilot_token_from_gh_cli(self.github_host)
        if not token_value:
            raise RuntimeError(
                f"Unable to refresh {self.display_name} token. Re-run `worker login {self.name}`."
            )

        return OAuthToken(
            access_token=token_value,
            token_type=token.token_type,
            provider=self.name,
            scope=token.scope,
        )


class GitHubCopilotOAuth(_GitHubCliOAuth):
    name = "github_copilot"
    display_name = "GitHub Copilot"


class GitHubCopilotEnterpriseOAuth(_GitHubCliOAuth):
    name = "github_copilot_enterprise"
    display_name = "GitHub Copilot Enterprise"


# ── Registry ──────────────────────────────────────────────────────

OAUTH_PROVIDERS: dict[str, type[OAuthProvider]] = {
    "anthropic": AnthropicOAuth,
    "openai": OpenAIOAuth,
    "github_copilot": GitHubCopilotOAuth,
    "github_copilot_enterprise": GitHubCopilotEnterpriseOAuth,
}


def get_oauth_provider(
    name: str,
    *,
    config: Any | None = None,
    token_store: TokenStore | None = None,
) -> OAuthProvider | None:
    """Get an OAuth provider by name, or None if not supported."""
    resolved_name = _resolve_provider_id(name)
    cls = OAUTH_PROVIDERS.get(resolved_name)
    if cls is None:
        return None

    kwargs: dict[str, Any] = {}
    if token_store is not None:
        kwargs["token_store"] = token_store
    if issubclass(cls, _GitHubCliOAuth):
        kwargs["github_host"] = get_github_copilot_host(config, resolved_name)
    return cls(**kwargs)


def list_oauth_provider_names() -> list[str]:
    """Return the providers that currently support OAuth."""
    return sorted(OAUTH_PROVIDERS)
