"""Helpers shared by the command line and the OpenJarvis tools. No OpenJarvis import here."""

from __future__ import annotations

import json
import os

from jarvis_sidekick import paths, resume
from jarvis_sidekick.profile import github


def resume_profile(path: str = "") -> resume.Profile:
    path = path or os.environ.get("SIDEKICK_RESUME", "")
    if not path:
        raise ValueError("No resume given. Pass resume_path or set SIDEKICK_RESUME.")
    return resume.load(path)


def load_answers() -> dict[str, str]:
    """Pre-approved answers to screening questions: ~/.jarvis-sidekick/answers.json (label text -> answer)."""
    path = paths.home() / "answers.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def gather_skills(skills: list[str] | None = None, resume_path: str = "", github_user: str = "") -> list[str]:
    found = list(skills or [])
    if resume_path or os.environ.get("SIDEKICK_RESUME"):
        found += resume_profile(resume_path).skills
    if github_user:
        profile = github.summary(github_user)
        found += list(profile["languages"]) + resume.find_skills(" ".join(profile["topics"]).replace("-", " "))
    if not found:
        raise ValueError("Give skills, a resume or a GitHub user to rank ideas against.")
    return found
