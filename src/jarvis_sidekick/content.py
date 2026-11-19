"""Check a draft (YouTube title or description, LinkedIn post, script) against your own publishing rules.

The rules that never change are built in. The private ones (names that must
never appear in public, links every description must carry) live in
``~/.jarvis-sidekick/content.json`` on your machine and are never committed:

    {
      "blocklist": ["Employer Name", "Project Codename"],
      "watchlist": ["old spelling of my name"],
      "required_in_youtube_description": ["https://github.com/you/repo", "Narration: AI-generated voice."]
    }
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from jarvis_sidekick import paths

KINDS = ("youtube-title", "youtube-description", "linkedin", "script")
AI_VOICE_LINE = "Narration: AI-generated voice."

_PROMISE = re.compile(
    r"\b(?:makes?|is|are|be|become|becomes|get|gets|stay|stays|keeps?|ensures?|guarantees?)\b[^.\n]{0,40}?"
    r"\b(compliant|certified|audit[- ]proof)\b|\b(guaranteed)\b",
    re.IGNORECASE,
)
_NEGATED = re.compile(r"\b(?:not|never|no|cannot|can't|doesn't|does not|won't)\b[^.\n]{0,50}$", re.IGNORECASE)
_RETAIL = re.compile(r"\b(retail(?:er)?s?|fashion|merchandis\w+|store planning|buying team)\b", re.IGNORECASE)
_NUMBER_CLAIM = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:%|percent|x faster|x fewer)|\b\d[\d,]*\+?\s+(?:clients|teams|companies|engineers|students|"
    r"subscribers|views)\b",
    re.IGNORECASE,
)
_ATTACK = re.compile(r"\b(payloads?|exploit(?:s|ed|ing)?|bypass(?:es|ing)?|reverse shell|sql injection string)\b", re.IGNORECASE)
_HASHTAG = re.compile(r"(?<!\w)#\w+")


@dataclass
class Finding:
    level: str  # "error" blocks publishing; "warning" asks you to look
    rule: str
    message: str
    line: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def load_rules() -> dict[str, list[str]]:
    path = paths.home() / "content.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("content.json must hold an object")  # noqa: TRY004
    return {key: [str(v) for v in value] for key, value in data.items() if isinstance(value, list)}


def _line_of(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def _phrases(text: str, phrases: list[str], level: str, rule: str, message: str) -> list[Finding]:
    found = []
    for phrase in phrases:
        if not phrase.strip():
            continue
        for hit in re.finditer(rf"(?<!\w){re.escape(phrase.strip())}(?!\w)", text, re.IGNORECASE):
            # The phrase itself is not repeated: findings may be sent over chat.
            found.append(Finding(level, rule, message, _line_of(text, hit.start())))
    return found


def check(text: str, kind: str = "script", rules: dict[str, list[str]] | None = None) -> list[Finding]:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    rules = load_rules() if rules is None else rules
    found = _phrases(text, rules.get("blocklist", []), "error", "blocklist",
                     "names something on your private blocklist")
    found += _phrases(text, rules.get("watchlist", []), "warning", "watchlist",
                      "uses a phrase on your watchlist")

    for hit in _PROMISE.finditer(text):
        before = text[max(0, hit.start() - 60):hit.start() + len(hit.group(0))]
        if _NEGATED.search(before[: before.lower().rfind((hit.group(1) or hit.group(2)).lower())]):
            continue  # "does not make you compliant" is the honest sentence
        found.append(Finding("error", "promise", f"promises an outcome ('{hit.group(1) or hit.group(2)}'); "
                             "describe the practice instead", _line_of(text, hit.start())))
    for hit in _RETAIL.finditer(text):
        found.append(Finding("warning", "example-domain", f"'{hit.group(1)}': examples must be invented and not "
                             "set in retail", _line_of(text, hit.start())))
    for hit in _NUMBER_CLAIM.finditer(text):
        found.append(Finding("warning", "number", f"'{hit.group(0).strip()}': is this number real and sourced?",
                             _line_of(text, hit.start())))
    for hit in _ATTACK.finditer(text):
        found.append(Finding("warning", "security-tone", f"'{hit.group(1)}': keep security content defensive",
                             _line_of(text, hit.start())))

    words, hashtags = len(text.split()), len(_HASHTAG.findall(text))
    if kind == "youtube-title" and len(text.strip()) > 60:
        found.append(Finding("error", "length", f"title is {len(text.strip())} characters; the limit is 60"))
    if kind == "linkedin" and not 80 <= words <= 150:
        found.append(Finding("error", "length", f"post is {words} words; keep it between 80 and 150"))
    if kind in ("linkedin", "youtube-description") and hashtags > 3:
        found.append(Finding("error", "hashtags", f"{hashtags} hashtags; use at most 3"))
    if kind == "youtube-description":
        required = rules.get("required_in_youtube_description") or [AI_VOICE_LINE]
        if AI_VOICE_LINE not in required:
            required = [*required, AI_VOICE_LINE]
        for item in required:
            if item.lower() not in text.lower():
                label = item if item == AI_VOICE_LINE else "a required link or line from content.json"
                found.append(Finding("error", "required", f"missing: {label}"))
    return sorted(found, key=lambda f: (f.level != "error", f.line))


def passed(findings: list[Finding]) -> bool:
    return not any(f.level == "error" for f in findings)
