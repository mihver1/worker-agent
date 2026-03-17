"""Tests for raw JSON Schema passthrough across provider adapters."""

from __future__ import annotations

from artel_ai.models import ToolDef
from artel_ai.providers.anthropic import _build_tools as build_anthropic_tools
from artel_ai.providers.bedrock import _build_tools as build_bedrock_tools
from artel_ai.providers.google import _build_tools as build_google_tools
from artel_ai.providers.openai_compat import (
    _build_responses_tools as build_openai_responses_tools,
)
from artel_ai.providers.openai_compat import _build_tools as build_openai_tools
from artel_ai.tool_schema import json_schema_to_gemini_schema, tool_input_schema


def _nested_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "payload": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["text"],
            },
            "repeat": {"type": "integer", "default": 1},
        },
        "required": ["payload"],
    }


def _defs_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "payload": {"$ref": "#/$defs/Payload"},
            "repeat": {"type": "integer", "default": 1},
        },
        "required": ["payload"],
        "$defs": {
            "Payload": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["text"],
            }
        },
    }


def test_tool_input_schema_prefers_raw_json_schema():
    tool = ToolDef(
        name="demo",
        description="demo",
        parameters=[],
        input_schema=_nested_schema(),
    )

    assert tool_input_schema(tool) == _nested_schema()


def test_tool_input_schema_resolves_local_refs():
    tool = ToolDef(
        name="demo",
        description="demo",
        parameters=[],
        input_schema=_defs_schema(),
    )

    schema = tool_input_schema(tool)

    assert "$defs" not in schema
    assert schema["properties"]["payload"]["properties"]["text"]["type"] == "string"
    assert schema["properties"]["payload"]["properties"]["tags"]["items"]["type"] == "string"


def test_json_schema_to_gemini_schema_handles_nested_objects_and_arrays():
    converted = json_schema_to_gemini_schema(_nested_schema())

    assert converted["type"] == "OBJECT"
    assert converted["properties"]["payload"]["type"] == "OBJECT"
    assert converted["properties"]["payload"]["properties"]["text"]["type"] == "STRING"
    assert converted["properties"]["payload"]["properties"]["tags"]["type"] == "ARRAY"
    assert converted["properties"]["payload"]["properties"]["tags"]["items"]["type"] == "STRING"
    assert converted["properties"]["repeat"]["type"] == "INTEGER"


def test_provider_builders_preserve_raw_json_schema():
    schema = _nested_schema()
    tool = ToolDef(
        name="demo",
        description="demo",
        parameters=[],
        input_schema=schema,
    )

    anthropic = build_anthropic_tools([tool])[0]["input_schema"]
    openai = build_openai_tools([tool])[0]["function"]["parameters"]
    openai_responses = build_openai_responses_tools([tool])[0]["parameters"]
    bedrock = build_bedrock_tools([tool])[0]["toolSpec"]["inputSchema"]["json"]
    google = build_google_tools([tool])[0]["functionDeclarations"][0]["parameters"]

    assert anthropic == schema
    assert openai == schema
    assert openai_responses == schema
    assert bedrock == schema
    assert google["properties"]["payload"]["type"] == "OBJECT"
    assert google["properties"]["payload"]["properties"]["tags"]["items"]["type"] == "STRING"


def test_provider_builders_resolve_local_refs_for_raw_json_schema():
    tool = ToolDef(
        name="demo",
        description="demo",
        parameters=[],
        input_schema=_defs_schema(),
    )

    anthropic = build_anthropic_tools([tool])[0]["input_schema"]
    openai = build_openai_tools([tool])[0]["function"]["parameters"]
    openai_responses = build_openai_responses_tools([tool])[0]["parameters"]
    bedrock = build_bedrock_tools([tool])[0]["toolSpec"]["inputSchema"]["json"]
    google = build_google_tools([tool])[0]["functionDeclarations"][0]["parameters"]

    assert "$defs" not in anthropic
    assert anthropic["properties"]["payload"]["properties"]["text"]["type"] == "string"
    assert openai["properties"]["payload"]["properties"]["text"]["type"] == "string"
    assert openai_responses["properties"]["payload"]["properties"]["text"]["type"] == "string"
    assert bedrock["properties"]["payload"]["properties"]["text"]["type"] == "string"
    assert google["properties"]["payload"]["type"] == "OBJECT"
    assert google["properties"]["payload"]["properties"]["tags"]["items"]["type"] == "STRING"
