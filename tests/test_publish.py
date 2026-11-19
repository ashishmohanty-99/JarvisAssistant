from __future__ import annotations

import json

import httpx
import pytest
import respx

from jarvis_sidekick import paths, publish
from jarvis_sidekick.bot import Bot, Commander

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
GOOD_TEXT = """Check a spec in 3 steps before you approve it
Three checks before the agent builds, in a made-up library app.
1. Read each requirement. 2. Run /sdd-verify.
Narration: AI-generated voice.
Tags: spec driven development, ai coding"""
TOKEN = "123456789:" + "A" * 35
TG = f"https://api.telegram.org/bot{TOKEN}"


@pytest.fixture
def google(monkeypatch):
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "client-id.apps.googleusercontent.com")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "GOCSPX-very-secret")
    with respx.mock(assert_all_called=False) as router:
        router.device = router.post(publish.DEVICE_URL).mock(return_value=httpx.Response(200, json={
            "device_code": "dev-code-123", "user_code": "ABCD-EFGH", "verification_url": "https://www.google.com/device",
            "interval": 5, "expires_in": 1800}))
        router.token = router.post(publish.TOKEN_URL)
        yield router


def _connect():
    publish._save_token({"refresh_token": "1//refresh-secret"})


# ---- signing in


def test_device_sign_in_waits_then_stores_only_the_refresh_token_privately(google):
    with httpx.Client() as client:
        login = publish.start_login(client, now=1000.0)
        assert (login.user_code, login.verification_url) == ("ABCD-EFGH", "https://www.google.com/device")
        sent = dict(pair.split("=") for pair in google.device.calls.last.request.content.decode().split("&"))
        assert sent["scope"].endswith("auth%2Fyoutube") and "client_secret" not in sent

        assert publish.poll_login(client, login, now=1002.0) == "waiting" and not google.token.called  # too early
        google.token.mock(return_value=httpx.Response(428, json={"error": "authorization_pending"}))
        assert publish.poll_login(client, login, now=1006.0) == "waiting"
        google.token.mock(return_value=httpx.Response(403, json={"error": "slow_down"}))
        assert publish.poll_login(client, login, now=1012.0) == "waiting" and login.interval == 10

        google.token.mock(return_value=httpx.Response(200, json={"access_token": "ya29.short", "refresh_token": "1//r"}))
        assert publish.poll_login(client, login, now=1030.0) == "done"
    saved = paths.home() / "youtube-token.json"
    assert json.loads(saved.read_text()) == {"refresh_token": "1//r"} and oct(saved.stat().st_mode & 0o777) == "0o600"
    assert publish.connected()


@pytest.mark.parametrize("answer, message", [
    (httpx.Response(403, json={"error": "access_denied"}), "declined"),
    (httpx.Response(400, json={"error": "invalid_client", "error_description": "GOCSPX-very-secret"}), "invalid_client"),
])
def test_sign_in_failures_are_named_without_leaking_secrets(google, answer, message):
    google.token.mock(return_value=answer)
    with httpx.Client() as client:
        login = publish.start_login(client, now=0.0)
        with pytest.raises(publish.PublishError, match=message) as caught:
            publish.poll_login(client, login, now=10.0)
        assert "GOCSPX" not in str(caught.value) and "dev-code" not in str(caught.value)
        with pytest.raises(publish.PublishError, match="expired"):
            publish.poll_login(client, login, now=5000.0)
    assert not publish.connected()


def test_missing_client_settings_are_explained(monkeypatch):
    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
    assert "YOUTUBE_CLIENT_ID" in Commander().handle("/youtube")


# ---- the draft


def test_draft_text_splits_title_description_and_tags():
    draft = publish.save_video(MP4)
    draft.set_text(GOOD_TEXT)
    assert draft.title == "Check a spec in 3 steps before you approve it"
    assert draft.description.endswith("Narration: AI-generated voice.") and "Tags:" not in draft.description
    assert draft.tags == ["spec driven development", "ai coding"] and draft.problems() == []


def test_only_real_video_files_are_accepted_and_a_new_one_replaces_the_old():
    with pytest.raises(publish.PublishError, match="not an MP4"):
        publish.save_video(b"MZ\x90\x00 a renamed program")
    first = publish.save_video(MP4)
    first.set_text(GOOD_TEXT)
    first.save()
    publish.save_video(MP4 + b"\x01")
    assert publish.Draft.load().title == ""  # the earlier text did not carry over to a different video


# ---- uploading


def _mock_upload(router, applied="public"):
    router.token.mock(return_value=httpx.Response(200, json={"access_token": "ya29.access-secret"}))
    start = router.post(publish.UPLOAD_URL).mock(return_value=httpx.Response(
        200, headers={"location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=xyz"}))
    put = router.put("https://www.googleapis.com/upload/youtube/v3/videos?upload_id=xyz").mock(
        return_value=httpx.Response(200, json={"id": "vid12345678", "status": {"privacyStatus": applied}}))
    return start, put


def test_upload_sends_metadata_then_the_file_and_reports_what_youtube_applied(google):
    _connect()
    start, put = _mock_upload(google, applied="private")
    draft = publish.save_video(MP4)
    draft.set_text(GOOD_TEXT)
    with httpx.Client() as client:
        result = publish.upload(draft, "public", client)

    assert result == {"id": "vid12345678", "url": "https://youtu.be/vid12345678", "privacy": "private"}
    request = start.calls.last.request
    body = json.loads(request.content)
    assert request.url.params["uploadType"] == "resumable" and request.headers["authorization"] == "Bearer ya29.access-secret"
    assert request.headers["x-upload-content-length"] == str(len(MP4))
    assert body["snippet"]["title"].startswith("Check a spec") and body["snippet"]["categoryId"] == "28"
    assert body["status"] == {"privacyStatus": "public", "selfDeclaredMadeForKids": False}
    assert put.calls.last.request.content == MP4 and put.calls.last.request.headers["content-type"] == "video/mp4"


def test_upload_refuses_a_draft_that_breaks_a_rule_before_contacting_google(google):
    _connect()
    draft = publish.save_video(MP4)
    draft.set_text("A title\nThis skill makes your team compliant.")  # a promise, and no AI-voice line
    with httpx.Client() as client, pytest.raises(publish.PublishError, match="publishing rules"):
        publish.upload(draft, "public", client)
    assert not google.token.called


def test_expired_sign_in_and_quota_errors_are_explained(google):
    _connect()
    draft = publish.save_video(MP4)
    draft.set_text(GOOD_TEXT)
    with httpx.Client() as client:
        google.token.mock(return_value=httpx.Response(200, json={"access_token": "ya29.a"}))
        google.post(publish.UPLOAD_URL).mock(return_value=httpx.Response(403, json={
            "error": {"code": 403, "errors": [{"reason": "quotaExceeded"}], "message": "details with ya29.a"}}))
        with pytest.raises(publish.PublishError, match="quotaExceeded") as caught:
            publish.upload(draft, "private", client)
        assert "ya29" not in str(caught.value)

        google.token.mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
        with pytest.raises(publish.PublishError, match="7 days"):
            publish.upload(draft, "private", client)
    assert not publish.connected()  # a dead sign-in is removed so /youtube starts cleanly


# ---- through the bot


def _bot_and_router(router, commander=None):
    router.sent = router.post(f"{TG}/sendMessage").mock(return_value=httpx.Response(200, json={"ok": True}))
    router.post(f"{TG}/sendChatAction").mock(return_value=httpx.Response(200, json={"ok": True}))
    router.post(f"{TG}/getFile").mock(return_value=httpx.Response(200, json={"ok": True, "result": {"file_path": "videos/f.mp4"}}))
    router.get(f"https://api.telegram.org/file/bot{TOKEN}/videos/f.mp4").mock(return_value=httpx.Response(200, content=MP4))
    return Bot(TOKEN, "4242", commander or Commander())


def _update(update_id=1, sender=4242, **message):
    return {"update_id": update_id, "message": {"chat": {"id": sender, "type": "private"}, "from": {"id": sender}, **message}}


def _last(router):
    return json.loads(router.sent.calls.last.request.content)


def test_video_with_caption_is_summarised_and_one_tap_publishes_it(google):
    _connect()
    _mock_upload(google, applied="public")
    bot = _bot_and_router(google)
    bot.process(_update(video={"file_id": "v1", "file_size": len(MP4)}, caption=GOOD_TEXT))
    offer = _last(google)
    assert "Title: Check a spec in 3 steps" in offer["text"] and offer["text"].endswith("Publish it?")
    assert [b["text"] for b in offer["reply_markup"]["keyboard"][0]] == ["Publish public", "Upload private", "Skip"]

    bot.process(_update(2, text="Publish public"))
    assert _last(google)["text"] == "Uploaded: https://youtu.be/vid12345678\nVisibility: public"
    assert publish.Draft.load() is None and not (paths.home() / "outbox" / "video.mp4").exists()


def test_video_first_then_text_and_the_private_lock_is_explained(google):
    _connect()
    _mock_upload(google, applied="private")
    bot = _bot_and_router(google)
    bot.process(_update(document={"file_id": "v1", "file_size": len(MP4), "mime_type": "video/mp4", "file_name": "s.mp4"}))
    assert "Now send the text as one message" in _last(google)["text"]
    bot.process(_update(2, text=GOOD_TEXT))
    assert _last(google)["text"].endswith("Publish it?")
    bot.process(_update(3, text="Publish public"))
    assert "YouTube set it to private" in _last(google)["text"] and "change Visibility to Public" in _last(google)["text"]


def test_rule_breaking_text_gets_no_publish_buttons_until_fixed(google):
    _connect()
    bot = _bot_and_router(google)
    bot.process(_update(video={"file_id": "v1", "file_size": len(MP4)}, caption="Title\nNo voice line here."))
    refused = _last(google)
    assert "ERROR: missing: Narration: AI-generated voice." in refused["text"] and "reply_markup" not in refused
    bot.process(_update(2, text="/publish public"))
    assert "publishing rules" in _last(google)["text"] and not google.token.called
    bot.process(_update(3, text="/text\n" + GOOD_TEXT))
    assert _last(google)["text"].endswith("Publish it?")


def test_voice_strangers_and_oversized_files_cannot_publish(google):
    _connect()
    _mock_upload(google)
    commander = Commander()
    bot = _bot_and_router(google, commander)
    bot.process(_update(video={"file_id": "v1", "file_size": len(MP4)}, caption=GOOD_TEXT))

    assert "I can give you the market briefing" in commander.handle("Publish public", spoken=True)  # heard, not tapped
    assert "I can give you the market briefing" in commander.handle("/publish public", spoken=True)
    bot.process(_update(2, sender=999, text="Publish public"))
    assert bot.dropped == 1 and publish.Draft.load() is not None

    bot.process(_update(3, video={"file_id": "big", "file_size": 60_000_000}))
    assert "at most 20 MB" in _last(google)["text"]
    assert commander.handle("Skip") == "Skipped. The video is deleted from the server." and publish.Draft.load() is None


def test_connecting_from_the_chat(google):
    commander = Commander()
    reply = commander.handle("/youtube")
    assert "ABCD-EFGH" in reply and "https://www.google.com/device" in reply and commander.youtube_login
    commander.youtube_login.next_poll = 0
    google.token.mock(return_value=httpx.Response(428, json={"error": "authorization_pending"}))
    assert commander.youtube_poll() is None and commander.youtube_login
    commander.youtube_login.next_poll = 0
    google.token.mock(return_value=httpx.Response(200, json={"access_token": "a", "refresh_token": "1//r"}))
    assert commander.youtube_poll().startswith("YouTube is connected") and commander.youtube_login is None
    assert commander.handle("/youtube").startswith("YouTube is connected.")
    assert commander.handle("/youtube disconnect").startswith("YouTube disconnected") and not publish.connected()
