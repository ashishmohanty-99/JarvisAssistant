from __future__ import annotations

import httpx
import pytest
import respx

from jarvis_sidekick.speech.elevenlabs_stt import API_URL, ElevenLabsSpeechBackend

KEY = "sk_test_secret_value"


@respx.mock
def test_transcribe_sends_key_model_and_audio_and_parses_words():
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "language_code": "en",
                "language_probability": 0.98,
                "text": " Find me python jobs ",
                "words": [
                    {"text": "Find", "start": 0.0, "end": 0.3, "type": "word"},
                    {"text": " ", "start": 0.3, "end": 0.35, "type": "spacing"},
                    {"text": "me", "start": 0.35, "end": 1.2, "type": "word"},
                ],
            },
        )
    )
    result = ElevenLabsSpeechBackend(api_key=KEY).transcribe(b"RIFFdata", format="wav", language="en")

    request = route.calls.last.request
    body = request.content
    assert request.headers["xi-api-key"] == KEY
    assert b'name="model_id"\r\n\r\nscribe_v2' in body
    assert b'name="language_code"\r\n\r\nen' in body
    assert b'filename="audio.wav"' in body and b"RIFFdata" in body
    assert result.text == "Find me python jobs"
    assert result.language == "en" and result.confidence == 0.98
    assert [s.text for s in result.segments] == ["Find", "me"]
    assert result.duration_seconds == 1.2


@respx.mock
def test_multichannel_response_uses_first_transcript():
    respx.post(API_URL).mock(return_value=httpx.Response(200, json={"transcripts": [{"text": "hello"}]}))
    assert ElevenLabsSpeechBackend(api_key=KEY).transcribe(b"x").text == "hello"


@respx.mock
@pytest.mark.parametrize("response", [httpx.Response(401, json={"detail": KEY}), httpx.Response(200, text="nope")])
def test_errors_never_contain_the_key(response):
    respx.post(API_URL).mock(return_value=response)
    with pytest.raises(RuntimeError) as caught:
        ElevenLabsSpeechBackend(api_key=KEY).transcribe(b"x")
    assert KEY not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


@respx.mock
def test_network_error_is_reported_by_category_only():
    respx.post(API_URL).mock(side_effect=httpx.ConnectError(f"boom {KEY}"))
    with pytest.raises(RuntimeError, match="ConnectError") as caught:
        ElevenLabsSpeechBackend(api_key=KEY).transcribe(b"x")
    assert KEY not in str(caught.value)


def test_without_key_it_is_unhealthy_and_refuses():
    backend = ElevenLabsSpeechBackend()
    assert backend.health() is False
    with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY not set"):
        backend.transcribe(b"x")


def test_key_with_control_characters_is_rejected_without_echo():
    with pytest.raises(ValueError) as caught:
        ElevenLabsSpeechBackend(api_key="abc\ndef")
    assert "abc" not in str(caught.value)
