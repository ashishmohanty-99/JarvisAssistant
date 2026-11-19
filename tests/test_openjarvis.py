"""Integration with a real OpenJarvis install."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("openjarvis")

from openjarvis.core.config import JarvisConfig
from openjarvis.core.registry import SpeechRegistry, ToolRegistry
from openjarvis.core.types import ToolCall
from openjarvis.speech._discovery import get_speech_backend
from openjarvis.tools._stubs import ToolExecutor

import jarvis_sidekick
from jarvis_sidekick.oj_tools import TOOLS
from jarvis_sidekick.speech.elevenlabs_stt import ElevenLabsSpeechBackend


def test_register_is_idempotent_and_adds_everything():
    names = jarvis_sidekick.register()
    assert jarvis_sidekick.register() == names
    assert SpeechRegistry.get("elevenlabs") is ElevenLabsSpeechBackend
    assert all(ToolRegistry.contains(name) for name in names)
    assert len(names) == len(TOOLS) == 9


def test_jarvis_resolves_elevenlabs_stt_from_config(monkeypatch):
    jarvis_sidekick.register()
    config = JarvisConfig()
    config.speech.backend = "elevenlabs"
    assert get_speech_backend(config) is None  # no key: unhealthy, so not selected

    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")
    backend = get_speech_backend(config)
    assert isinstance(backend, ElevenLabsSpeechBackend)


def test_auto_discovery_prefers_elevenlabs_when_a_key_is_set(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")
    jarvis_sidekick.register()
    config = JarvisConfig()
    assert config.speech.backend == "auto"
    assert isinstance(get_speech_backend(config), ElevenLabsSpeechBackend)


def _call(name: str, **arguments):
    tools = [ToolRegistry.get(name)()]
    return ToolExecutor(tools).execute(ToolCall(id="1", name=name, arguments=json.dumps(arguments)))


def test_tool_runs_through_the_jarvis_executor():
    jarvis_sidekick.register()
    result = _call("investment_plan", monthly=10000, years=10, risk="moderate")
    assert result.success
    payload = json.loads(result.content)
    assert payload["invested"] == 1_200_000
    assert sum(row["percent"] for row in payload["allocation"]) == 100


def test_tool_failure_is_a_message_not_a_crash():
    jarvis_sidekick.register()
    result = _call("investment_plan", monthly=-5, years=10)
    assert not result.success and "monthly must be positive" in result.content


def test_every_tool_has_a_valid_schema_and_apply_needs_confirmation():
    for tool_cls in TOOLS:
        function = tool_cls().to_openai_function()["function"]
        assert function["name"] == tool_cls.name and function["description"]
        assert function["parameters"]["type"] == "object"
        assert set(function["parameters"]["required"]) <= set(function["parameters"]["properties"])
    assert ToolRegistry.get("job_apply")().spec.requires_confirmation is True


def test_jarvis_refuses_job_apply_when_nobody_can_confirm():
    jarvis_sidekick.register()
    result = _call("job_apply", job_id="abc", confirm=True)
    assert not result.success and "requires confirmation" in result.content
