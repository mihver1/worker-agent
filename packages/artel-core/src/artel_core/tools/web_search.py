"""Web search tool backed by DuckDuckGo HTML search."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import httpx
from artel_ai.models import ToolDef, ToolParam

from artel_core.tools import Tool
from artel_core.tools.web_guard import (
    llm_safe_summarize_untrusted_web_content,
    summarize_untrusted_web_content,
    validate_web_url_access,
    wrap_untrusted_web_content,
)

_SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"
_DEFAULT_LIMIT = 5
_MAX_LIMIT = 10
_USER_AGENT = "Artel/0.1 (+https://github.com/mihver1/artel)"


@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""


class _DuckDuckGoHTMLParser(HTMLParser):
    """Extract result titles, URLs, and snippets from DuckDuckGo HTML pages."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[SearchResult] = []
        self._in_result_link = False
        self._in_result_snippet = False
        self._link_href = ""
        self._link_parts: list[str] = []
        self._snippet_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        classes = set((attr_map.get("class") or "").split())

        if tag == "a" and ({"result__a", "result-link"} & classes):
            self._in_result_link = True
            self._link_href = attr_map.get("href") or ""
            self._link_parts = []
            return

        if ({"result__snippet", "result-snippet"} & classes) and tag in {
            "a",
            "div",
            "span",
            "td",
        }:
            self._in_result_snippet = True
            self._snippet_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_result_link:
            self._in_result_link = False
            title = _normalize_whitespace("".join(self._link_parts))
            url = _clean_result_url(self._link_href)
            if title and url:
                self.results.append(SearchResult(title=title, url=url))
            self._link_href = ""
            self._link_parts = []
            return

        if tag in {"a", "div", "span", "td"} and self._in_result_snippet:
            self._in_result_snippet = False
            snippet = _normalize_whitespace("".join(self._snippet_parts))
            if snippet:
                for result in reversed(self.results):
                    if not result.snippet:
                        result.snippet = snippet
                        break
            self._snippet_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_result_link:
            self._link_parts.append(data)
        elif self._in_result_snippet:
            self._snippet_parts.append(data)


def _normalize_whitespace(value: str) -> str:
    return " ".join(unescape(value).split())


def _clean_result_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = f"https:{href}"
    if href.startswith("/"):
        href = urljoin("https://duckduckgo.com", href)

    parsed = urlsplit(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path == "/l/":
        uddg = parse_qs(parsed.query).get("uddg")
        if uddg:
            return unquote(uddg[0])
    return href


def parse_search_results(html_text: str, *, limit: int = _DEFAULT_LIMIT) -> list[SearchResult]:
    parser = _DuckDuckGoHTMLParser()
    parser.feed(html_text)

    results: list[SearchResult] = []
    seen: set[str] = set()
    for result in parser.results:
        if not result.url or result.url in seen:
            continue
        seen.add(result.url)
        results.append(result)
        if len(results) >= limit:
            break
    return results


async def _fetch_search_results_html(query: str) -> str:
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept-Language": "en-US,en;q=0.8",
    }
    async with httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        headers=headers,
    ) as client:
        response = await client.get(_SEARCH_ENDPOINT, params={"q": query})
        response.raise_for_status()
        return response.text


class WebSearchTool(Tool):
    """Search the public web and return top results."""

    name = "web_search"
    description = (
        "Search the public web and return the top matching results with titles, URLs, and snippets."
    )

    async def execute(self, **kwargs: Any) -> str:
        query = str(kwargs.get("query", "")).strip()
        limit = int(kwargs.get("limit", _DEFAULT_LIMIT) or _DEFAULT_LIMIT)
        limit = max(1, min(limit, _MAX_LIMIT))
        allow_domains = str(kwargs.get("allow_domains", "") or "")
        deny_domains = str(kwargs.get("deny_domains", "") or "")
        mode = str(kwargs.get("mode", "full") or "full").strip().lower()

        if not query:
            return "Error: query must not be empty"
        if mode not in {"full", "summary", "llm_summary", "strict"}:
            return "Error: mode must be 'full', 'summary', 'llm_summary', or 'strict'"

        try:
            html_text = await _fetch_search_results_html(query)
            results = parse_search_results(html_text, limit=limit)
        except httpx.HTTPStatusError as exc:
            return f"Error running web search: HTTP {exc.response.status_code}"
        except httpx.HTTPError as exc:
            return f"Error running web search: {exc}"
        except Exception as exc:
            return f"Error running web search: {exc}"

        filtered: list[SearchResult] = []
        skipped = 0
        for result in results:
            allowed, _reason = validate_web_url_access(
                result.url,
                allow_domains=allow_domains,
                deny_domains=deny_domains,
            )
            if allowed:
                filtered.append(result)
            else:
                skipped += 1

        if not filtered:
            if skipped:
                return "No search results available after domain filtering."
            return "No search results found."

        lines = [f"Search results for: {query}"]
        if skipped:
            lines.append(f"Filtered out {skipped} result(s) by domain policy.")
        for index, result in enumerate(filtered, start=1):
            snippet_text = result.snippet or "(no snippet provided)"
            if mode == "summary":
                snippet_text = summarize_untrusted_web_content(
                    snippet_text, max_lines=4, max_chars=800
                )
            elif mode in {"llm_summary", "strict"}:
                snippet_text = await llm_safe_summarize_untrusted_web_content(
                    snippet_text,
                    max_chars=800,
                    fallback_max_lines=4,
                )

            snippet_block = wrap_untrusted_web_content(
                source=f"web_search result snippet ({mode})",
                url=result.url,
                title=result.title,
                body=snippet_text,
            )
            lines.append(f"{index}. {result.title}")
            lines.append(f"   {result.url}")
            for snippet_line in snippet_block.splitlines():
                lines.append(f"   {snippet_line}")
        return "\n".join(lines)

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParam(name="query", type="string", description="Search query"),
                ToolParam(
                    name="limit",
                    type="integer",
                    description="Maximum number of results to return (default: 5)",
                    required=False,
                ),
                ToolParam(
                    name="mode",
                    type="string",
                    description=(
                        "Snippet mode: 'full', 'summary', 'llm_summary', or "
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


__all__ = ["SearchResult", "WebSearchTool", "parse_search_results"]
