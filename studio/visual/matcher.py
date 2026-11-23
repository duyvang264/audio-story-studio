"""Asset matching: reuse library images, never with the wrong character or location."""
from __future__ import annotations


def compatible(scene: dict, asset: dict, style_profile: str) -> bool:
    if asset.get("status", "active") != "active" or asset["style_profile"] != style_profile:
        return False
    # Wrong location is never acceptable when both sides know their location.
    if scene.get("location_id") and asset.get("location_id") and asset["location_id"] != scene["location_id"]:
        return False
    # An image must not show a character who is not in the scene.
    if not set(asset.get("character_ids") or []) <= set(scene.get("characters") or []):
        return False
    return True


def score(scene: dict, asset: dict, *, previous_asset_id: int | None = None) -> float:
    s = 0.0
    if scene.get("location_id") and asset.get("location_id") == scene["location_id"]:
        s += 3
    sc = set(scene.get("characters") or [])
    ac = set(asset.get("character_ids") or [])
    s += 2 * (len(sc & ac) / len(sc) if sc else (1 if not ac else 0))
    if asset.get("mood") == scene.get("mood"):
        s += 1
    tags = set(asset.get("tags") or [])
    s += 0.25 * len(tags & {scene.get("mood"), scene.get("location_id"), *sc})
    if previous_asset_id is not None and asset["id"] == previous_asset_id:
        s -= 1.5  # prefer variety between consecutive scenes
    return s


def classify(scene: dict, asset: dict) -> str:
    same_loc = bool(scene.get("location_id")) and asset.get("location_id") == scene.get("location_id")
    same_chars = set(asset.get("character_ids") or []) == set(scene.get("characters") or [])
    if same_loc and same_chars:
        return "exact"
    if same_loc:
        return "compatible"
    return "generic"


def best_match(scene: dict, assets: list[dict], style_profile: str, *,
               previous_asset_id: int | None = None) -> tuple[dict | None, str | None, float]:
    cands = [a for a in assets if compatible(scene, a, style_profile)]
    if not cands:
        return None, None, 0.0
    best = max(cands, key=lambda a: (score(scene, a, previous_asset_id=previous_asset_id), -a["id"]))
    return best, classify(scene, best), score(scene, best, previous_asset_id=previous_asset_id)


def generation_key(scene: dict) -> str:
    return f"{scene.get('location_id') or 'none'}|{','.join(sorted(scene.get('characters') or []))}|{scene.get('mood')}"
