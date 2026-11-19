"""jarvis-sidekick: voice, research, job hunting and market briefings on top of OpenJarvis."""

from __future__ import annotations

import os

__version__ = "0.1.0"


def register() -> list[str]:
    """Add the ElevenLabs speech-to-text backend and the sidekick tools to OpenJarvis.

    Safe to call more than once. Returns the tool names now available.
    """
    import openjarvis.speech
    import openjarvis.tools  # noqa: F401
    from openjarvis.core.registry import SpeechRegistry, ToolRegistry
    from openjarvis.speech import _discovery

    from jarvis_sidekick.oj_tools import TOOLS
    from jarvis_sidekick.speech.elevenlabs_stt import ElevenLabsSpeechBackend

    if not SpeechRegistry.contains("elevenlabs"):
        SpeechRegistry.register_value("elevenlabs", ElevenLabsSpeechBackend)
    # With a key present, "auto" should pick ElevenLabs before the local and other cloud backends.
    if os.environ.get("ELEVENLABS_API_KEY") and "elevenlabs" not in _discovery.DISCOVERY_ORDER:
        _discovery.DISCOVERY_ORDER.insert(0, "elevenlabs")
    for tool in TOOLS:
        if not ToolRegistry.contains(tool.name):
            ToolRegistry.register_value(tool.name, tool)
    return [tool.name for tool in TOOLS]
