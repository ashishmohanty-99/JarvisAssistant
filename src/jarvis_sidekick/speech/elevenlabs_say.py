"""Minimal ElevenLabs text-to-speech call, so spoken replies work without OpenJarvis installed."""

from __future__ import annotations

import os
import re

import httpx

from jarvis_sidekick.speech.elevenlabs_stt import _clean_key

DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # "George", the same default OpenJarvis uses
MAX_CHARS = 2500


def synthesize(text: str, *, api_key: str = "", voice_id: str = "") -> bytes:
    """MP3 bytes for *text* (cut to MAX_CHARS: speech is billed per character)."""
    key = _clean_key(api_key or os.environ.get("ELEVENLABS_API_KEY", ""))
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY not set")
    voice = voice_id or os.environ.get("ELEVENLABS_VOICE_ID", "") or DEFAULT_VOICE
    if not re.fullmatch(r"[A-Za-z0-9]{10,40}", voice):
        raise ValueError("ELEVENLABS_VOICE_ID is not a valid voice id")
    try:
        response = httpx.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
            headers={"xi-api-key": key},
            params={"output_format": "mp3_44100_128"},
            json={"text": text[:MAX_CHARS], "model_id": os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")},
            timeout=120.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"ElevenLabs speech failed (HTTP {exc.response.status_code})") from None
    except httpx.HTTPError as exc:
        raise RuntimeError(f"ElevenLabs speech failed ({type(exc).__name__})") from None
    return response.content
