"""Upload a video to your own YouTube channel, after you approve it.

Authorisation uses Google's device flow, which suits a server with no browser:
the bot shows a short code, you enter it at google.com/device on any device,
and the server receives a token for your channel. The token is stored in the
state folder, readable only by the bot's account. The client id and secret come
from a Google Cloud project you create (YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET).

Nothing is uploaded without an explicit, typed or tapped, per-video approval.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from jarvis_sidekick import content, paths

DEVICE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPE = "https://www.googleapis.com/auth/youtube"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
CATEGORY_SCIENCE_AND_TECHNOLOGY = "28"
PRIVACY = ("public", "unlisted", "private")
MAX_VIDEO_BYTES = 20_000_000  # what a Telegram bot is allowed to download


class PublishError(RuntimeError):
    """Something on the YouTube side failed. The message never contains a token or secret."""


def _credentials() -> tuple[str, str]:
    client_id, secret = os.environ.get("YOUTUBE_CLIENT_ID", ""), os.environ.get("YOUTUBE_CLIENT_SECRET", "")
    if not (client_id and secret):
        raise PublishError("Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET first (see deploy/YOUTUBE.md).")
    return client_id, secret


def _post(client: httpx.Client, url: str, what: str, **kwargs) -> httpx.Response:
    try:
        return client.post(url, **kwargs)
    except httpx.HTTPError as exc:
        raise PublishError(f"{what} failed ({type(exc).__name__})") from None


def _error_code(response: httpx.Response) -> str:
    """Google's short error name, e.g. 'authorization_pending' or 'quotaExceeded'. Never the body."""
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        reasons = [e.get("reason") for e in error.get("errors") or [] if isinstance(e, dict) and e.get("reason")]
        return str(reasons[0] if reasons else error.get("status") or f"HTTP {response.status_code}")
    return f"HTTP {response.status_code}"


# ------------------------------------------------------------------ authorisation


@dataclass
class DeviceLogin:
    """A device-flow sign-in in progress."""

    device_code: str
    user_code: str
    verification_url: str
    interval: float
    expires_at: float
    next_poll: float = 0.0


def _token_path() -> Path:
    return paths.home() / "youtube-token.json"


def connected() -> bool:
    return _token_path().exists()


def disconnect() -> None:
    _token_path().unlink(missing_ok=True)


def start_login(client: httpx.Client, now: float | None = None) -> DeviceLogin:
    client_id, _ = _credentials()
    response = _post(client, DEVICE_URL, "starting the sign-in", data={"client_id": client_id, "scope": SCOPE})
    if response.status_code != 200:
        raise PublishError(f"Google refused to start the sign-in ({_error_code(response)}). "
                           "The OAuth client must be of type 'TVs and Limited Input devices'.")
    body = response.json()
    now = time.time() if now is None else now
    interval = float(body.get("interval", 5))
    return DeviceLogin(
        device_code=body["device_code"], user_code=body["user_code"],
        verification_url=body.get("verification_url") or body.get("verification_uri") or "https://www.google.com/device",
        interval=interval, expires_at=now + float(body.get("expires_in", 1800)), next_poll=now + interval,
    )


def poll_login(client: httpx.Client, login: DeviceLogin, now: float | None = None) -> str:
    """One check on a pending sign-in. Returns 'waiting', 'done', or raises when it cannot succeed any more."""
    now = time.time() if now is None else now
    if now >= login.expires_at:
        raise PublishError("The sign-in code expired. Send /youtube to get a new one.")
    if now < login.next_poll:
        return "waiting"
    client_id, secret = _credentials()
    response = _post(client, TOKEN_URL, "checking the sign-in", data={
        "client_id": client_id, "client_secret": secret, "device_code": login.device_code, "grant_type": DEVICE_GRANT})
    if response.status_code == 200:
        body = response.json()
        if not body.get("refresh_token"):
            raise PublishError("Google returned no long-lived token; remove the app's access in your Google account "
                               "and send /youtube again.")
        _save_token({"refresh_token": body["refresh_token"]})
        return "done"
    code = _error_code(response)
    if code == "authorization_pending":
        login.next_poll = now + login.interval
        return "waiting"
    if code == "slow_down":
        login.interval += 5
        login.next_poll = now + login.interval
        return "waiting"
    if code == "access_denied":
        raise PublishError("You declined the request on Google's page.")
    raise PublishError(f"Google rejected the sign-in ({code}).")


def _save_token(token: dict) -> None:
    path = _token_path()
    path.write_text(json.dumps(token), encoding="utf-8")
    path.chmod(0o600)


def _access_token(client: httpx.Client) -> str:
    try:
        refresh = json.loads(_token_path().read_text(encoding="utf-8"))["refresh_token"]
    except (OSError, ValueError, KeyError):
        raise PublishError("YouTube is not connected. Send /youtube first.") from None
    client_id, secret = _credentials()
    response = _post(client, TOKEN_URL, "refreshing the YouTube sign-in", data={
        "client_id": client_id, "client_secret": secret, "refresh_token": refresh, "grant_type": "refresh_token"})
    if response.status_code != 200:
        code = _error_code(response)
        if code == "invalid_grant":
            disconnect()
            raise PublishError("Google no longer accepts the saved sign-in (it expires after 7 days while the "
                               "Google project is in 'Testing'). Send /youtube to connect again.")
        raise PublishError(f"Google refused to refresh the sign-in ({code}).")
    return response.json()["access_token"]


# ------------------------------------------------------------------ the draft awaiting approval


@dataclass
class Draft:
    """One video waiting for a title, a description and your approval."""

    video: str = ""
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)

    @staticmethod
    def _path() -> Path:
        return paths.home() / "outbox" / "draft.json"

    @classmethod
    def load(cls) -> Draft | None:
        try:
            data = json.loads(cls._path().read_text(encoding="utf-8"))
            draft = cls(**{k: data[k] for k in ("video", "title", "description", "tags") if k in data})
        except (OSError, ValueError, TypeError):
            return None
        return draft if draft.video and Path(draft.video).exists() else None

    def save(self) -> None:
        self._path().parent.mkdir(exist_ok=True)
        self._path().write_text(json.dumps(self.__dict__, ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def discard(cls) -> None:
        folder = cls._path().parent
        if folder.exists():
            for item in folder.iterdir():
                item.unlink()

    def set_text(self, text: str) -> None:
        """First line is the title; the rest is the description. A trailing 'Tags: a, b' line sets the tags."""
        lines = [line.rstrip() for line in text.strip().splitlines()]
        if lines and lines[-1].lower().startswith("tags:"):
            self.tags = [t.strip() for t in lines.pop()[5:].split(",") if t.strip()][:15]
        self.title = lines[0].strip() if lines else ""
        self.description = "\n".join(lines[1:]).strip()

    def problems(self) -> list[content.Finding]:
        """Your publishing rules, applied to the title and the description. Errors block the upload."""
        if not self.title:
            return [content.Finding("error", "required", "no title yet")]
        found = content.check(self.title, "youtube-title") + content.check(self.description, "youtube-description")
        return sorted(found, key=lambda f: f.level != "error")

    def summary(self) -> str:
        size = Path(self.video).stat().st_size / 1_000_000
        lines = [f"Video ready ({size:.1f} MB).", f"Title: {self.title or '(none yet)'}",
                 f"Description: {len(self.description)} characters, {len(self.tags)} tags"]
        for finding in self.problems():
            where = f" line {finding.line}" if finding.line else ""
            lines.append(f"{finding.level.upper()}{where}: {finding.message}")
        return "\n".join(lines)


def save_video(data: bytes) -> Draft:
    """Keep one incoming video as the new draft, replacing any earlier one."""
    if not (data[4:8] == b"ftyp" or data.startswith(b"\x1aE\xdf\xa3")):  # MP4/MOV or WebM/MKV
        raise PublishError("That file is not an MP4, MOV or WebM video.")
    Draft.discard()
    folder = paths.home() / "outbox"
    folder.mkdir(exist_ok=True)
    target = folder / "video.mp4"
    target.write_bytes(data)
    target.chmod(0o600)
    draft = Draft(video=str(target))
    draft.save()
    return draft


# ------------------------------------------------------------------ upload


def upload(draft: Draft, privacy: str, client: httpx.Client) -> dict:
    """Send the draft to YouTube. Returns {'id', 'url', 'privacy'} with the privacy YouTube actually applied."""
    if privacy not in PRIVACY:
        raise PublishError(f"privacy must be one of {', '.join(PRIVACY)}")
    if any(f.level == "error" for f in draft.problems()):
        raise PublishError("The draft breaks one of your publishing rules; fix it first.")
    video = Path(draft.video)
    size = video.stat().st_size
    token = _access_token(client)
    metadata = {
        "snippet": {"title": draft.title, "description": draft.description, "tags": draft.tags,
                    "categoryId": CATEGORY_SCIENCE_AND_TECHNOLOGY},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    start = _post(client, UPLOAD_URL, "starting the upload",
                  params={"uploadType": "resumable", "part": "snippet,status"},
                  headers={"Authorization": f"Bearer {token}", "X-Upload-Content-Type": "video/mp4",
                           "X-Upload-Content-Length": str(size)},
                  json=metadata)
    session = start.headers.get("location", "")
    if start.status_code != 200 or not session.startswith("https://www.googleapis.com/"):
        raise PublishError(f"YouTube refused the upload ({_error_code(start)}).")
    try:
        done = client.put(session, content=video.read_bytes(),
                          headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"}, timeout=600.0)
    except httpx.HTTPError as exc:
        raise PublishError(f"sending the video failed ({type(exc).__name__})") from None
    if done.status_code not in (200, 201):
        raise PublishError(f"YouTube rejected the video ({_error_code(done)}).")
    body = done.json()
    applied = str((body.get("status") or {}).get("privacyStatus") or "unknown")
    return {"id": body["id"], "url": f"https://youtu.be/{body['id']}", "privacy": applied}
