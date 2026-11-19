"""ElevenLabs speech-to-text (Scribe) backend for OpenJarvis.

OpenJarvis ships ElevenLabs text-to-speech but no ElevenLabs transcription. This
adds the missing half so voice chat can use one provider in both directions.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

try:
    from openjarvis.speech._stubs import Segment, SpeechBackend, TranscriptionResult
except ImportError:  # usable for plain transcription without OpenJarvis installed
    from dataclasses import dataclass, field

    @dataclass
    class Segment:  # type: ignore[no-redef]
        text: str
        start: float
        end: float
        confidence: float | None = None

    @dataclass
    class TranscriptionResult:  # type: ignore[no-redef]
        text: str
        language: str | None = None
        confidence: float | None = None
        duration_seconds: float = 0.0
        segments: list[Segment] = field(default_factory=list)

    class SpeechBackend:  # type: ignore[no-redef]
        backend_id = ""


API_URL = "https://api.elevenlabs.io/v1/speech-to-text"
DEFAULT_MODEL = "scribe_v2"
_MIME = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
    "webm": "audio/webm",
    "m4a": "audio/mp4",
    "mp4": "video/mp4",
}


def _clean_key(value: str) -> str:
    key = value.strip()
    if any(not 33 <= ord(char) <= 126 for char in key):
        raise ValueError("ElevenLabs API key contains unsupported characters")
    return key


class ElevenLabsSpeechBackend(SpeechBackend):
    """Cloud speech-to-text using ElevenLabs Scribe."""

    backend_id = "elevenlabs"

    def __init__(self, api_key: str | None = None, model: str = "") -> None:
        self._api_key = _clean_key(api_key or os.environ.get("ELEVENLABS_API_KEY", ""))
        self._model = model or os.environ.get("ELEVENLABS_STT_MODEL", "") or DEFAULT_MODEL

    def transcribe(self, audio: bytes, *, format: str = "wav", language: str | None = None) -> TranscriptionResult:
        if not self._api_key:
            raise RuntimeError("ELEVENLABS_API_KEY not set")
        data: dict[str, Any] = {"model_id": self._model}
        if language:
            data["language_code"] = language
        try:
            response = httpx.post(
                API_URL,
                headers={"xi-api-key": self._api_key},
                data=data,
                files={"file": (f"audio.{format}", audio, _MIME.get(format, "application/octet-stream"))},
                timeout=120.0,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"ElevenLabs transcription failed (HTTP {exc.response.status_code})") from None
        except (httpx.HTTPError, ValueError) as exc:
            # Keep the category only: messages can carry request headers.
            raise RuntimeError(f"ElevenLabs transcription failed ({type(exc).__name__})") from None
        return _to_result(payload)

    def health(self) -> bool:
        return bool(self._api_key)

    def supported_formats(self) -> list[str]:
        return list(_MIME)


def _to_result(payload: Any) -> TranscriptionResult:
    # Multichannel requests answer {"transcripts": [...]}; use the first channel.
    if isinstance(payload, dict) and isinstance(payload.get("transcripts"), list) and payload["transcripts"]:
        payload = payload["transcripts"][0]
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise RuntimeError("ElevenLabs transcription failed (unexpected response)")  # noqa: TRY004
    words = [w for w in payload.get("words") or [] if isinstance(w, dict) and w.get("type", "word") == "word"]
    segments = [
        Segment(text=str(w.get("text", "")), start=float(w.get("start") or 0.0), end=float(w.get("end") or 0.0))
        for w in words
    ]
    probability = payload.get("language_probability")
    return TranscriptionResult(
        text=payload["text"].strip(),
        language=payload.get("language_code"),
        confidence=float(probability) if isinstance(probability, (int, float)) else None,
        duration_seconds=max((s.end for s in segments), default=0.0),
        segments=segments,
    )
