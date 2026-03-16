"""Web fetch tool for retrieving and simplifying public web pages."""

from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import httpx
from worker_ai.models import ToolDef, ToolParam

from worker_core.tools import Tool
from worker_core.tools.web_guard import (
    llm_safe_summarize_untrusted_web_content,
    summarize_untrusted_web_content,
    validate_web_url_access,
    wrap_untrusted_web_content,
)

_MAX_CHARS = 20_000
_USER_AGENT = "Artel/0.1 (+https://github.com/mihver1/worker-agent)"


class _HTMLTextExtractor(HTMLParser):
    """Convert HTML into plain text while skipping noisy tags."""

    _SKIP_TAGS = {"script", "style", "noscript", "svg"}
    _BLOCK_TAGS = {
        "p",
        "div",
        "section",
        "article",
        "main",
        "header",
        "footer",
        "aside",
        "li",
        "ul",
        "ol",
        "br",
        "tr",
        "table",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "pre",
        "blockquote",
    }

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._parts: list[str] = []
        self.title = ""
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            self._title_parts = []
            return
        if self._skip_depth == 0 and tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag == "title" and self._in_title:
            self._in_title = False
            self.title = _collapse_text("".join(self._title_parts))
            return
        if self._skip_depth == 0 and tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        raw = unescape("".join(self._parts))
        lines = [_collapse_text(line) for line in raw.splitlines()]
        cleaned = [line for line in lines if line]
        return "\n".join(cleaned)


def _collapse_text(value: str) -> str:
    return " ".join(value.split())


async def _fetch_url(url: str) -> httpx.Response:
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html, text/plain, application/xhtml+xml;q=0.9, */*;q=0.1",
    }
    async with httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        headers=headers,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response


def _is_allowed_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


async def _render_response(
    url: str,
    response: httpx.Response,
    *,
    max_chars: int = _MAX_CHARS,
    mode: str = "full",
) -> str:
    content_type = (response.headers.get("content-type") or "").lower()
    body = response.text

    title = ""
    text = body
    if "html" in content_type or "<html" in body.lower():
        extractor = _HTMLTextExtractor()
        extractor.feed(body)
        title = extractor.title
        text = extractor.text()
    else:
        text = body.strip()

    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
        truncated = True

    if mode == "summary":
        text = summarize_untrusted_web_content(text, max_lines=8, max_chars=min(max_chars, 4000))
    elif mode == "llm_summary":
        text = await llm_safe_summarize_untrusted_web_content(
            text,
            max_chars=min(max_chars, 4000),
        )
    elif mode == "strict":
        text = await llm_safe_summarize_untrusted_web_content(
            text,
            max_chars=min(max_chars, 3000),
        )

    rendered = wrap_untrusted_web_content(
        source=f"web_fetch ({mode})",
        url=url,
        title=title,
        body=text or "(empty response body)",
    )
    if content_type:
        rendered = f"Content-Type: {content_type}\n{rendered}"
    if truncated:
        rendered += "\n... (truncated)"
    return rendered


class WebFetchTool(Tool):
    """Fetch a public web page and return simplified text."""

    name = "web_fetch"
    description = "Fetch a public URL and return a readable text version of the page content."

    async def execute(self, **kwargs: Any) -> str:
        url = str(kwargs.get("url", "")).strip()
        max_chars = int(kwargs.get("max_chars", _MAX_CHARS) or _MAX_CHARS)
        max_chars = max(100, min(max_chars, _MAX_CHARS))
        mode = str(kwargs.get("mode", "full") or "full").strip().lower()
        allow_domains = str(kwargs.get("allow_domains", "") or "")
        deny_domains = str(kwargs.get("deny_domains", "") or "")

        if not url:
            return "Error: url must not be empty"
        if not _is_allowed_url(url):
            return "Error: url must be an absolute http or https URL"
        if mode not in {"full", "summary", "llm_summary", "strict"}:
            return "Error: mode must be 'full', 'summary', 'llm_summary', or 'strict'"
        allowed, reason = validate_web_url_access(
            url,
            allow_domains=allow_domains,
            deny_domains=deny_domains,
        )
        if not allowed:
            return f"Error: {reason}"

        try:
            response = await _fetch_url(url)
            return await _render_response(url, response, max_chars=max_chars, mode=mode)
        except httpx.HTTPStatusError as exc:
            return f"Error fetching URL: HTTP {exc.response.status_code}"
        except httpx.HTTPError as exc:
            return f"Error fetching URL: {exc}"
        except Exception as exc:
            return f"Error fetching URL: {exc}"

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParam(name="url", type="string", description="Absolute URL to fetch"),
                ToolParam(
                    name="max_chars",
                    type="integer",
                    description="Maximum number of response characters to return (default: 20000)",
                    required=False,
                ),
                ToolParam(
                    name="mode",
                    type="string",
                    description=(
                        "Response mode: 'full', 'summary', 'llm_summary', or "
                        "'strict' (default: full)"
                    ),
                    required=False,
                    enum=["full", "summary", "llm_summary", "strict"],
                ),
                ToolParam(
                    name="allow_domains",
                    type="string",
                    description="Optional comma-separated domain allowlist",
                    required=False,
                ),
                ToolParam(
                    name="deny_domains",
                    type="string",
                    description="Optional comma-separated domain denylist",
                    required=False,
                ),
            ],
        )


__all__ = ["WebFetchTool"]
