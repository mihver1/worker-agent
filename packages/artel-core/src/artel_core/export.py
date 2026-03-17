"""Export session history to HTML.

Renders the conversation as a standalone HTML file with
inline CSS styling.  No external dependencies (no Jinja2).
"""

from __future__ import annotations

import html
from datetime import UTC, datetime

from artel_ai.models import Message, Role

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
         max-width: 800px; margin: 40px auto; padding: 0 20px;
         background: #1e1e2e; color: #cdd6f4; }}
  h1 {{ color: #89b4fa; border-bottom: 1px solid #313244; padding-bottom: 8px; }}
  .meta {{ color: #6c7086; font-size: 0.85em; margin-bottom: 24px; }}
  .msg {{ margin: 12px 0; padding: 10px 14px; border-radius: 8px; }}
  .user {{ background: #313244; border-left: 3px solid #89b4fa; }}
  .assistant {{ background: #1e1e2e; border: 1px solid #313244; }}
  .tool {{ background: #1e1e2e; color: #6c7086; font-style: italic; font-size: 0.9em; }}
  .system {{ background: #1e1e2e; color: #a6adc8; font-size: 0.85em; }}
  .role {{ font-weight: 600; margin-bottom: 4px; text-transform: uppercase;
           font-size: 0.75em; letter-spacing: 0.05em; color: #a6adc8; }}
  pre {{ background: #313244; padding: 10px; border-radius: 4px;
         overflow-x: auto; font-size: 0.9em; }}
  code {{ font-family: 'JetBrains Mono', 'Fira Code', monospace; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="meta">{meta}</div>
{messages}
</body>
</html>
"""


def _role_class(role: Role) -> str:
    return {
        Role.USER: "user",
        Role.ASSISTANT: "assistant",
        Role.TOOL: "tool",
        Role.SYSTEM: "system",
    }.get(role, "system")


def _render_message(msg: Message) -> str:
    role_label = msg.role.value.capitalize()
    css_class = _role_class(msg.role)
    content = html.escape(msg.content or "")

    # Simple markdown-ish: wrap ``` blocks in <pre>
    lines = content.split("\n")
    output_lines: list[str] = []
    in_code = False
    for line in lines:
        if line.startswith("```"):
            if in_code:
                output_lines.append("</code></pre>")
                in_code = False
            else:
                output_lines.append("<pre><code>")
                in_code = True
        else:
            output_lines.append(line)
    if in_code:
        output_lines.append("</code></pre>")
    rendered = "<br>".join(output_lines)

    # Tool result
    if msg.tool_result:
        tool_content = html.escape(msg.tool_result.content[:500])
        rendered += f'<div class="tool">→ {tool_content}</div>'

    # Tool calls
    if msg.tool_calls:
        calls = ", ".join(tc.name for tc in msg.tool_calls)
        rendered += f'<div class="tool">[tools: {calls}]</div>'

    return f'<div class="msg {css_class}"><div class="role">{role_label}</div>{rendered}</div>'


def export_html(
    messages: list[Message],
    *,
    title: str = "Artel Session",
    model: str = "",
    session_id: str = "",
) -> str:
    """Render a list of messages as a standalone HTML string."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    meta_parts = [now]
    if model:
        meta_parts.append(f"Model: {model}")
    if session_id:
        meta_parts.append(f"Session: {session_id[:8]}")
    meta_parts.append(f"{len(messages)} messages")
    meta = " · ".join(meta_parts)

    rendered = "\n".join(_render_message(m) for m in messages if m.content or m.tool_result)

    return _HTML_TEMPLATE.format(title=title, meta=meta, messages=rendered)
