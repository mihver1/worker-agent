from __future__ import annotations

import base64

from worker_ai.models import ImageAttachment, Message, Role
from worker_ai.providers.anthropic import _build_messages as build_anthropic_messages
from worker_ai.providers.google import _build_contents as build_google_contents
from worker_ai.providers.openai_compat import _build_messages as build_openai_messages
from worker_ai.providers.openai_compat import _build_responses_input


def _png_attachment(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(b"png-data")
    return ImageAttachment(path=str(image_path), mime_type="image/png", name="shot.png")


def test_openai_chat_builder_includes_image_parts(tmp_path):
    attachment = _png_attachment(tmp_path)
    messages = [Message(role=Role.USER, content="Look", attachments=[attachment])]

    built = build_openai_messages(messages)

    assert built[0]["role"] == "user"
    assert built[0]["content"][0] == {"type": "text", "text": "Look"}
    image_url = built[0]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,")
    assert image_url.endswith(base64.b64encode(b"png-data").decode("ascii"))


def test_openai_responses_builder_includes_input_image(tmp_path):
    attachment = _png_attachment(tmp_path)
    messages = [Message(role=Role.USER, content="Look", attachments=[attachment])]

    instructions, items = _build_responses_input(messages)

    assert instructions is None
    assert items[0]["type"] == "message"
    assert items[0]["content"][0] == {"type": "input_text", "text": "Look"}
    assert items[0]["content"][1]["type"] == "input_image"
    assert items[0]["content"][1]["image_url"].startswith("data:image/png;base64,")


def test_anthropic_builder_includes_image_blocks(tmp_path):
    attachment = _png_attachment(tmp_path)
    messages = [Message(role=Role.USER, content="Look", attachments=[attachment])]

    system, built = build_anthropic_messages(messages)

    assert system is None
    assert built[0]["role"] == "user"
    assert built[0]["content"][0] == {"type": "text", "text": "Look"}
    assert built[0]["content"][1]["type"] == "image"
    assert built[0]["content"][1]["source"]["media_type"] == "image/png"
    assert built[0]["content"][1]["source"]["data"] == base64.b64encode(b"png-data").decode("ascii")


def test_google_builder_includes_inline_data_parts(tmp_path):
    attachment = _png_attachment(tmp_path)
    messages = [Message(role=Role.USER, content="Look", attachments=[attachment])]

    system, built = build_google_contents(messages)

    assert system is None
    assert built[0]["role"] == "user"
    assert built[0]["parts"][0] == {"text": "Look"}
    assert built[0]["parts"][1]["inlineData"]["mimeType"] == "image/png"
    assert built[0]["parts"][1]["inlineData"]["data"] == base64.b64encode(b"png-data").decode(
        "ascii"
    )
