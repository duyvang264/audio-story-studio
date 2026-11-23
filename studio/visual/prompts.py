"""Prompt Builder: series-wide style profile + per-scene subject, location, mood, camera."""
from __future__ import annotations

import json
from functools import lru_cache

from ..config import PROJECT_ROOT


@lru_cache
def load_style_profiles() -> dict:
    return json.loads((PROJECT_ROOT / "config" / "style_profiles.json").read_text(encoding="utf-8"))


def get_profile(name: str) -> dict:
    profiles = load_style_profiles()
    if name not in profiles:
        raise KeyError(f"unknown style profile {name!r}")
    return profiles[name]


def build_prompt(*, characters: list[str], location_id: str | None, mood: str, script: dict,
                 style_profile: str) -> tuple[str, str]:
    profile = get_profile(style_profile)
    char_defs = {c["id"]: c for c in script.get("characters", [])}
    loc_defs = {l["id"]: l for l in script.get("locations", [])}
    subjects = [char_defs[c].get("visual", char_defs[c]["name"]) for c in characters if c in char_defs]
    location = loc_defs.get(location_id, {}).get("visual", "desolate dark fantasy landscape")
    if subjects:
        subject = " and ".join(subjects) + f", in {location}"
        consistency = "keep exact same character design, costume and colors as the series reference: " + \
                      "; ".join(subjects)
    else:
        subject = f"{location}, no people, environment only"
        consistency = ""
    parts = [
        subject,
        profile["mood_lighting"].get(mood, profile["mood_lighting"]["mysterious"]),
        profile["mood_camera"].get(mood, profile["mood_camera"]["mysterious"]),
        profile["base"],
        consistency,
        f"aspect ratio {profile['aspect_ratio']}, cinematic widescreen composition, no text",
    ]
    return ", ".join(p for p in parts if p), profile["negative"]
