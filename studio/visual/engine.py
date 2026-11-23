"""Visual Story Engine: scene_plan.json, image_generation_queue.json, scene_assets_map.json."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from .. import budget
from ..config import Settings, get_settings
from ..db import connect, log_event
from . import matcher
from .prompts import build_prompt
from .providers import ImageProviderError, get_image_provider
from .scene_planner import SceneRules, plan_scenes, accelerate_scenes


def _ep_dir(s: Settings, episode_id: int) -> Path:
    return s.storage / "episodes" / str(episode_id)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _assets(c, style_profile: str, provider: str) -> list[dict]:
    """Reusable library. Mock placeholders are only reused while the mock provider is active,
    so real episodes never pick up test images."""
    return c.execute("""SELECT * FROM visual_assets WHERE status='active' AND style_profile=%s
                        AND (provider <> 'mock' OR %s = 'mock') ORDER BY id""",
                     (style_profile, provider)).fetchall()


def new_images_used(c, episode_id: int, purpose: str = "generate") -> int:
    return c.execute("""SELECT COUNT(*) AS n FROM provider_costs WHERE episode_id=%s AND category='image'
                        AND purpose=%s AND status IN ('reserved','committed')""", (episode_id, purpose)).fetchone()["n"]


def _limit_for(purpose: str, s: Settings) -> int:
    return s.max_regen_images_per_episode if purpose == "regen" else s.max_new_images_per_episode


def plan_visuals(episode_id: int, settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    d = _ep_dir(s, episode_id)
    script = _load(d / "episode_script.json")
    timeline = _load(d / "transcript_timeline.json")
    rules = SceneRules(s.target_scene_duration_sec, s.min_scene_duration_sec, s.max_scene_duration_sec)
    scenes = plan_scenes(script, timeline, rules)
    if s.audio.accelerated_visuals:
        scenes = accelerate_scenes(scenes, timeline, s.audio.fast_visual_sec)
    style = script["style_profile"]
    for sc in scenes:
        sc["prompt"], sc["negative_prompt"] = build_prompt(
            characters=sc["characters"], location_id=sc["location_id"], mood=sc["mood"],
            script=script, style_profile=style)
        sc["generation_key"] = matcher.generation_key(sc)

    with connect() as c:
        library = _assets(c, style, s.image_provider)
        used = new_images_used(c, episode_id)
        prev_id = None
        needs: dict[str, dict] = {}
        for sc in scenes:
            asset, mtype, _ = matcher.best_match(sc, library, style, previous_asset_id=prev_id)
            sc["library_match"] = {"asset_id": asset["id"], "type": mtype} if asset else None
            if asset and mtype == "exact":
                prev_id = asset["id"]
                continue
            req = needs.setdefault(sc["generation_key"], {
                "key": sc["generation_key"], "prompt": sc["prompt"], "negative_prompt": sc["negative_prompt"],
                "characters": sc["characters"], "location_id": sc["location_id"], "mood": sc["mood"],
                "scene_nos": [], "priority_sec": 0.0})
            req["scene_nos"].append(sc["scene_no"])
            req["priority_sec"] += sc["duration"]
        allowed = max(0, s.max_new_images_per_episode - used)
        ordered = sorted(needs.values(), key=lambda r: -r["priority_sec"])
        queue = []
        for i, req in enumerate(ordered):
            req["request_id"] = hashlib.sha1(f"{episode_id}|{req['key']}".encode()).hexdigest()[:12]
            req["status"] = "pending" if i < allowed else "skipped_limit"
            req["aspect_ratio"] = "16:9"
            queue.append(req)

        c.execute("DELETE FROM scenes WHERE episode_id=%s", (episode_id,))
        for sc in scenes:
            c.execute("""INSERT INTO scenes (episode_id, scene_no, start_sec, end_sec, summary, characters, location_id,
                         mood, intensity, motion, prompt, negative_prompt) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                      (episode_id, sc["scene_no"], sc["start"], sc["end"], sc["summary"], sc["characters"],
                       sc["location_id"], sc["mood"], sc["intensity"], sc["motion"], sc["prompt"], sc["negative_prompt"]))
        log_event(f"visual plan: {len(scenes)} scenes, {sum(r['status']=='pending' for r in queue)} images to generate,"
                  f" {sum(r['status']=='skipped_limit' for r in queue)} over limit", episode_id=episode_id, conn=c)

    _dump(d / "scene_plan.json", {"episode_id": episode_id, "style_profile": style,
                                   "rules": vars(rules), "duration_sec": timeline["duration_sec"], "scenes": scenes})
    _dump(d / "image_generation_queue.json", {"episode_id": episode_id, "provider": s.image_provider,
                                               "max_new_images": s.max_new_images_per_episode, "requests": queue})
    return {"scenes": len(scenes), "to_generate": sum(r["status"] == "pending" for r in queue)}


def register_asset(c, *, src: Path, story: dict, kind: str, provider: str, prompt: str | None,
                   style_profile: str, character_ids: list[str], location_id: str | None, mood: str | None,
                   tags: list[str], storage: Path) -> dict:
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    sub = {"scene": "scenes", "environment": "environments", "character": "characters"}[kind]
    dest = storage / "assets" / sub / story["slug"] / f"{sha[:16]}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dest.resolve():
        shutil.copyfile(src, dest)
    row = c.execute("SELECT * FROM visual_assets WHERE path=%s", (str(dest),)).fetchone()
    if row:
        if row["status"] != "active":  # freshly generated again on purpose -> usable again
            row = c.execute("UPDATE visual_assets SET status='active' WHERE id=%s RETURNING *", (row["id"],)).fetchone()
        return row
    return c.execute("""INSERT INTO visual_assets (kind, path, sha256, provider, prompt, style_profile, story_id,
                        character_ids, location_id, mood, tags, width, height)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1920,1080) RETURNING *""",
                     (kind, str(dest), sha, provider, prompt, style_profile, story["id"], character_ids,
                      location_id, mood, tags)).fetchone()


def generate_visuals(episode_id: int, *, job_id: int | None = None, settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    d = _ep_dir(s, episode_id)
    qpath = d / "image_generation_queue.json"
    queue = _load(qpath)
    pending = [r for r in queue["requests"] if r["status"] in ("pending", "failed")]
    generated, failed = 0, []
    if pending:
        try:
            provider = get_image_provider(s)
        except ImageProviderError as e:
            raise RuntimeError(f"image provider unavailable: {e} (set IMAGE_PROVIDER=mock to continue)") from e
        with connect() as c:
            ep = c.execute("SELECT * FROM episodes WHERE id=%s", (episode_id,)).fetchone()
            story = c.execute("SELECT * FROM stories WHERE id=%s", (ep["story_id"],)).fetchone()
        tmp_dir = d / "generated"
        tmp_dir.mkdir(exist_ok=True)
        for req in pending:
            purpose = "regen" if req["key"].startswith("regen|") else "generate"
            with connect() as c:
                if new_images_used(c, episode_id, purpose) >= _limit_for(purpose, s):
                    req["status"] = "skipped_limit"
                    continue
            if provider.name == "codex" and budget.units_today("image", "codex") >= s.codex_max_images_per_day:
                req["status"] = "skipped_quota"
                failed.append(req["request_id"])
                continue
            res = budget.reserve("image", provider.name, 1, job_id=job_id, episode_id=episode_id,
                                 purpose=purpose, settings=s)
            out = tmp_dir / f"{req['request_id']}.png"
            try:
                meta = provider.generate(req["prompt"], req["negative_prompt"], out, mood=req["mood"],
                                         label=req["key"])
            except Exception as e:  # provider failures are recorded per request, not fatal for the batch
                budget.release(res, note=str(e)[:300])
                req["status"], req["error"] = "failed", str(e)[:500]
                failed.append(req["request_id"])
                continue
            budget.commit(res, note=json.dumps(meta))
            with connect() as c:
                asset = register_asset(
                    c, src=out, story=story, kind="scene", provider=provider.name, prompt=req["prompt"],
                    style_profile=queue.get("style_profile") or story["style_profile"],
                    character_ids=req["characters"], location_id=req["location_id"], mood=req["mood"],
                    tags=[t for t in [req["mood"], req["location_id"], *req["characters"]] if t], storage=s.storage)
                log_event(f"image generated ({provider.name}) for {req['key']}", episode_id=episode_id, job_id=job_id,
                          data={"asset_id": asset["id"]}, conn=c)
                if req["key"].startswith("regen|"):
                    c.execute("""UPDATE scenes SET asset_id=%s, match_type='regenerated', status='asset_ready',
                                 updated_at=now() WHERE episode_id=%s AND scene_no=ANY(%s)""",
                              (asset["id"], episode_id, req["scene_nos"]))
            req["status"], req["asset_id"] = "done", asset["id"]
            generated += 1
            _dump(qpath, queue)
        _dump(qpath, queue)
    mapping = resolve_scene_assets(episode_id, settings=s)
    return {"generated": generated, "failed": failed, **mapping}


def resolve_scene_assets(episode_id: int, settings: Settings | None = None) -> dict:
    """Assign an asset to every scene: exact > compatible > generic. Never a wrong character/location."""
    s = settings or get_settings()
    d = _ep_dir(s, episode_id)
    plan = _load(d / "scene_plan.json")
    style = plan["style_profile"]
    out, missing = [], []
    with connect() as c:
        library = _assets(c, style, s.image_provider)
        rows = {r["scene_no"]: r for r in c.execute("SELECT * FROM scenes WHERE episode_id=%s", (episode_id,)).fetchall()}
        prev_id = None
        for sc in plan["scenes"]:
            row = rows[sc["scene_no"]]
            if row["asset_id"] and row["status"] in ("asset_ready", "approved"):
                # explicitly assigned (e.g. regenerated) — keep it even if not in the active library
                asset = c.execute("SELECT * FROM visual_assets WHERE id=%s", (row["asset_id"],)).fetchone()
                mtype = row["match_type"]
            else:
                rejected = set(row["rejected_asset_ids"] or [])
                asset, mtype, _ = matcher.best_match(sc, [a for a in library if a["id"] not in rejected], style,
                                                     previous_asset_id=prev_id)
            if asset is None:
                missing.append(sc["scene_no"])
                out.append({"scene_no": sc["scene_no"], "asset_id": None, "path": None, "match_type": "missing"})
                continue
            prev_id = asset["id"]
            c.execute("""UPDATE scenes SET asset_id=%s, match_type=%s, status=CASE WHEN status='approved' THEN status
                         ELSE 'asset_ready' END, updated_at=now() WHERE id=%s""", (asset["id"], mtype, row["id"]))
            c.execute("""INSERT INTO asset_usage (asset_id, scene_id, episode_id) VALUES (%s,%s,%s)
                         ON CONFLICT DO NOTHING""", (asset["id"], row["id"], episode_id))
            out.append({"scene_no": sc["scene_no"], "asset_id": asset["id"], "path": asset["path"],
                        "match_type": mtype, "asset_characters": asset["character_ids"],
                        "asset_location": asset["location_id"]})
    _dump(d / "scene_assets_map.json", {"episode_id": episode_id, "scenes": out, "missing": missing})
    if missing:
        raise RuntimeError(f"no compatible asset for scenes {missing}; raise MAX_NEW_IMAGES_PER_EPISODE "
                           "or add environment assets to the library")
    return {"mapped": len(out), "missing": missing,
            "match_types": {t: sum(1 for o in out if o["match_type"] == t) for t in {o["match_type"] for o in out}}}


def request_scene_regen(episode_id: int, scene_no: int, settings: Settings | None = None) -> dict:
    """Queue a fresh image for one scene (counts toward MAX_NEW_IMAGES_PER_EPISODE)."""
    s = settings or get_settings()
    d = _ep_dir(s, episode_id)
    with connect() as c:
        row = c.execute("SELECT * FROM scenes WHERE episode_id=%s AND scene_no=%s", (episode_id, scene_no)).fetchone()
        if row is None:
            raise ValueError(f"scene {scene_no} not found in episode {episode_id}")
        if new_images_used(c, episode_id, "regen") >= s.max_regen_images_per_episode:
            raise ValueError(f"episode already used {s.max_regen_images_per_episode} scene regenerations "
                             "(MAX_REGEN_IMAGES_PER_EPISODE)")
        c.execute("""UPDATE scenes SET status='regen_requested', asset_id=NULL, version=version+1, updated_at=now(),
                     rejected_asset_ids = CASE WHEN %s::bigint IS NULL THEN rejected_asset_ids
                                               ELSE array_append(rejected_asset_ids, %s::bigint) END
                     WHERE id=%s""", (row["asset_id"], row["asset_id"], row["id"]))
        if row["asset_id"]:
            # keep the rejected image out of this scene's automatic re-matching
            c.execute("UPDATE visual_assets SET status='retired' WHERE id=%s AND NOT EXISTS ("
                      "SELECT 1 FROM asset_usage u WHERE u.asset_id=%s AND u.scene_id<>%s)",
                      (row["asset_id"], row["asset_id"], row["id"]))
            c.execute("DELETE FROM asset_usage WHERE scene_id=%s", (row["id"],))
    queue = _load(d / "image_generation_queue.json")
    req_id = hashlib.sha1(f"{episode_id}|regen|{scene_no}|{row['version'] + 1}".encode()).hexdigest()[:12]
    queue["requests"].append({
        "request_id": req_id, "key": f"regen|scene{scene_no}|v{row['version'] + 1}",
        # request_id makes every regeneration distinct (deterministic providers would otherwise
        # reproduce an image that was already rejected in another episode)
        "prompt": row["prompt"] + f", alternative composition variant {row['version'] + 1}-{req_id}",
        "negative_prompt": row["negative_prompt"], "characters": row["characters"],
        "location_id": row["location_id"], "mood": row["mood"], "scene_nos": [scene_no],
        "priority_sec": float(row["end_sec"] - row["start_sec"]), "status": "pending", "aspect_ratio": "16:9"})
    _dump(d / "image_generation_queue.json", queue)
    return {"scene_no": scene_no, "request_id": req_id}
