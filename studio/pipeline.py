"""Stage handlers executed by the worker, plus approval side effects and notifications."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx

from . import jobs, media, qc, render, shorts, story, timeline, uploaders
from .audio import produce_narration
from .config import Settings, get_settings
from .db import connect, jsonb, log_event
from .telegram import TelegramClient
from .visual import engine
from .audio_engine import production as audio_production
from .audio_engine.gate import AudioQCError, ListeningReviewRequired
from .audio_engine.io import dump as dump_json
from .horror_story import validate as validate_horror_story


class ApprovalError(RuntimeError):
    pass


# ------------------------------------------------------------------ helpers

def set_stage(episode_id: int, stage: str, status: str, error: str | None = None) -> None:
    with connect() as c:
        c.execute("""UPDATE episodes SET current_stage=%s, status=%s, last_error=%s, updated_at=now()
                     WHERE id=%s""", (stage, status, error, episode_id))


def chat_for(job: dict, s: Settings) -> str | None:
    return (job.get("payload") or {}).get("chat_id") or s.telegram_notify_chat_id or None


def notify(job: dict, text: str, s: Settings, reply_markup: dict | None = None) -> None:
    chat = chat_for(job, s)
    if chat:
        TelegramClient(s).send_message(chat, text, reply_markup)


def emit_event(event: str, job: dict, s: Settings, extra: dict | None = None) -> None:
    if not s.n8n_event_webhook_url:
        return
    try:
        httpx.post(s.n8n_event_webhook_url, timeout=10, headers={"X-API-Key": s.api_key},
                   json={"event": event, "job_id": job["id"], "type": job["type"],
                         "episode_id": job["episode_id"], **(extra or {})})
    except httpx.HTTPError:
        pass  # events are best-effort; the DB remains the source of truth


def request_approval(kind: str, *, story_id=None, episode_id=None, scene_id=None, payload=None) -> dict:
    with connect() as c:
        row = c.execute("""SELECT * FROM approvals WHERE kind=%s AND status='pending' AND COALESCE(story_id,0)=COALESCE(%s,0)
                           AND COALESCE(episode_id,0)=COALESCE(%s,0) AND COALESCE(scene_id,0)=COALESCE(%s,0)""",
                        (kind, story_id, episode_id, scene_id)).fetchone()
        if row:
            return row
        return c.execute("""INSERT INTO approvals (kind, story_id, episode_id, scene_id, payload)
                            VALUES (%s,%s,%s,%s,%s) RETURNING *""",
                         (kind, story_id, episode_id, scene_id, jsonb(payload or {}))).fetchone()


def decide(approval_id: int, approve: bool, user_id: int | None, *, chat_id=None, note: str | None = None) -> dict:
    """Serialize local approvals/canon updates across the native API and worker processes."""
    import fcntl
    lock = get_settings().storage / 'locks' / 'approvals.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            return _decide_locked(approval_id, approve, user_id, chat_id=chat_id, note=note)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _decide_locked(approval_id: int, approve: bool, user_id: int | None, *, chat_id=None, note: str | None = None) -> dict:
    """Apply an approval decision and its side effects. Idempotent for already-decided approvals."""
    s = get_settings()
    with connect() as c:
        ap = c.execute("SELECT * FROM approvals WHERE id=%s FOR UPDATE", (approval_id,)).fetchone()
        if ap is None:
            raise ApprovalError(f"approval {approval_id} not found")
        if ap["status"] != "pending":
            return {"approval": ap, "changed": False}
        if approve and ap['kind']=='content':
            from .horror_story import load_continuity, validate as check_continuity
            script_path=s.storage/'episodes'/str(ap['episode_id'])/'episode_script.json'
            if script_path.exists():
                script=json.loads(script_path.read_text())
                # Serialize approved canon changes for this story on the existing database transaction.
                c.execute('SELECT pg_advisory_xact_lock(%s)', (7243100+int(script.get('story_id') or 0),))
                quality=check_continuity(script,s.story_engine,continuity=load_continuity(script,s.storage))
                if any(i['type']=='continuity_conflict' for i in quality['issues']):
                    raise ApprovalError('Continuity conflict; review explicit continuity_updates')
        if approve and ap['kind'] == 'publish':
            records = c.execute('SELECT * FROM publishing_records WHERE approval_id=%s', (approval_id,)).fetchall()
            for rec in records:
                try:
                    audio_production.assert_publishable(rec['episode_id'], s, Path(rec['video_path']), short=rec['platform']=='tiktok')
                except AudioQCError as exc:
                    raise ApprovalError(str(exc)) from exc
        status = "approved" if approve else "rejected"
        ap = c.execute("""UPDATE approvals SET status=%s, decided_by=%s, decided_at=now(), note=%s
                          WHERE id=%s RETURNING *""", (status, user_id, note, approval_id)).fetchone()
        kind, ep = ap["kind"], ap["episode_id"]
        if kind == "rights":
            c.execute("UPDATE stories SET rights_status=%s, updated_at=now() WHERE id=%s", (status, ap["story_id"]))
        elif kind == "content":
            c.execute("UPDATE episodes SET status=%s, updated_at=now() WHERE id=%s",
                      ("approved" if approve else "preview_rejected", ep))
            if approve:
                jobs.enqueue("render_final", ep, payload={"chat_id": chat_id},
                             idempotency_key=f"render_final:{ep}:a{approval_id}", conn=c)
        elif kind == "scene":
            c.execute("UPDATE scenes SET status=%s, updated_at=now() WHERE id=%s",
                      ("approved" if approve else "rejected", ap["scene_id"]))
        elif kind == "publish":
            # A human approved publishing: upload where an uploader is configured, otherwise hand off manually.
            recs = c.execute("SELECT * FROM publishing_records WHERE approval_id=%s AND status='awaiting_approval'",
                             (approval_id,)).fetchall()
            for rec in recs:
                upload = approve and uploaders.configured(rec["platform"], s)
                new = ("approved" if upload else "ready_manual") if approve else "cancelled"
                c.execute("UPDATE publishing_records SET status=%s, updated_at=now() WHERE id=%s", (new, rec["id"]))
                if upload:
                    jobs.enqueue("publish_upload", ep, payload={"chat_id": chat_id, "record_id": rec["id"]},
                                 idempotency_key=f"publish_upload:{rec['id']}", max_attempts=3, conn=c)
        log_event(f"approval {kind} #{approval_id} {status} by {user_id}", episode_id=ep, conn=c)
    if approve and ap['kind']=='content':
        from .horror_story import commit_continuity
        script_path=s.storage/'episodes'/str(ap['episode_id'])/'episode_script.json'
        if script_path.exists():
            commit_continuity(json.loads(script_path.read_text()),s.storage,s.story_engine)
    return {"approval": ap, "changed": True}


def start_episode_pipeline(episode_id: int, chat_id=None, run: str = "r1") -> dict:
    with connect() as c:
        ep = c.execute("SELECT e.*, s.rights_status FROM episodes e JOIN stories s ON s.id=e.story_id WHERE e.id=%s",
                       (episode_id,)).fetchone()
        if ep is None:
            raise ValueError(f"episode {episode_id} not found")
        if ep["rights_status"] != "approved":
            raise ApprovalError("story rights not approved")
        job = jobs.enqueue("script_build", episode_id, payload={"chat_id": chat_id, "run": run},
                           idempotency_key=f"script_build:{episode_id}:{run}", conn=c)
        c.execute("UPDATE episodes SET status='queued', current_stage='script_build', updated_at=now() WHERE id=%s",
                  (episode_id,))
    return job


def chain_from(stage: str, episode_id: int, payload: dict, run: str) -> dict:
    return jobs.enqueue(stage, episode_id, payload={**payload, "run": run},
                        idempotency_key=f"{stage}:{episode_id}:{run}")


# ------------------------------------------------------------------ stages

def h_script_build(job, s):
    return story.build_episode_script(job["episode_id"])


def h_tts(job, s):
    payload=job.get('payload') or {}
    return produce_narration(job["episode_id"], job_id=job["id"], settings=s,
                             regenerate_chunks=payload.get('regenerate_chunks'), regeneration_key=payload.get('run'))


def h_story_validate(job, s):
    d = s.storage / 'episodes' / str(job['episode_id'])
    script = json.loads((d / 'episode_script.json').read_text())
    from .horror_story import load_continuity
    result = validate_horror_story(script, s.story_engine, continuity=load_continuity(script,s.storage))
    dump_json(d / 'story_quality_report.json', result)
    if result['status'] == 'FAIL':
        raise QCFailed('Story quality/continuity failed; see story_quality_report.json')
    return result


def h_scene_sound_analysis(job, s):
    d = s.storage / 'episodes' / str(job['episode_id'])
    script = json.loads((d / 'episode_script.json').read_text())
    result = {'sections': [{'section_no': sec['section_no'], 'text': ' '.join(p['text'] for p in sec['paragraphs'])}
                           for sec in script['sections']], 'timing': 'deferred until real TTS durations exist'}
    dump_json(d / 'scene_sound_analysis.json', result)
    return {'sections': len(result['sections'])}


def h_visual_plan(job, s):
    return engine.plan_visuals(job["episode_id"], settings=s)


def h_visual_generate(job, s):
    return engine.generate_visuals(job["episode_id"], job_id=job["id"], settings=s)


def h_timeline(job, s):
    d = s.storage / 'episodes' / str(job['episode_id'])
    try:
        audio_production.audio_fields(d)
    except AudioQCError:
        audio_production.prepare_episode(job['episode_id'], s)
    return timeline.compose(job["episode_id"], settings=s)


def _preview_path(s, ep):
    return s.storage / "previews" / str(ep) / f"preview_{s.preview_height}p.mp4"


def _final_path(s, ep):
    return s.storage / "output" / str(ep) / f"youtube_{s.final_height}p.mp4"


def h_render_preview(job, s):
    ep = job["episode_id"]
    out = _preview_path(s, ep)
    res = render.render_episode(ep, height=s.preview_height, out_path=out, crf=28, settings=s)
    with connect() as c:
        c.execute("UPDATE episodes SET preview_path=%s WHERE id=%s", (str(out), ep))
    return res


def h_qc_preview(job, s):
    report = qc.check_video(job["episode_id"], _preview_path(s, job["episode_id"]),
                            expect_height=s.preview_height, settings=s)
    if not report["passed"]:
        raise QCFailed("; ".join(report["issues"]))
    return {"passed": True, "info": report["info"]}


def h_qc_final(job, s):
    report = qc.check_video(job["episode_id"], _final_path(s, job["episode_id"]),
                            expect_height=s.final_height, settings=s)
    if not report["passed"]:
        raise QCFailed("; ".join(report["issues"]))
    audio_production.assert_publishable(job['episode_id'], s, _final_path(s, job['episode_id']))
    return {"passed": True, "info": report["info"]}


class QCFailed(RuntimeError):
    retryable = False


def scene_review_keyboard(episode_id: int, approval_id: int) -> dict:
    with connect() as c:
        scenes = c.execute("SELECT id, scene_no FROM scenes WHERE episode_id=%s ORDER BY scene_no",
                           (episode_id,)).fetchall()
    rows = [[{"text": f"✅ Cảnh {sc['scene_no']}", "callback_data": f"sc:{sc['id']}:ok"},
             {"text": f"🔁 Tạo lại cảnh {sc['scene_no']}", "callback_data": f"sc:{sc['id']}:regen"}]
            for sc in scenes[:40]]
    rows.append([{"text": "✅ Duyệt tập", "callback_data": f"ap:{approval_id}:y"},
                 {"text": "❌ Từ chối", "callback_data": f"ap:{approval_id}:n"}])
    return {"inline_keyboard": rows}


APPROVED_STATES = ("approved", "rendering_final", "ready_to_publish")


def h_notify_preview(job, s):
    ep = job["episode_id"]
    preview = _preview_path(s, ep)
    d = s.storage / "episodes" / str(ep)
    tl = json.loads((d / "timeline.json").read_text(encoding="utf-8"))
    with connect() as c:
        status = c.execute("SELECT status FROM episodes WHERE id=%s", (ep,)).fetchone()["status"]
    if (job.get("payload") or {}).get("resend") and status in APPROVED_STATES:
        chat = chat_for(job, s)
        if chat and preview.stat().st_size / 1e6 <= s.telegram_max_upload_mb:
            TelegramClient(s).send_video(chat, preview, f"🎬 Preview tập #{ep} (đã duyệt — chỉ gửi lại để xem).")
        elif chat:
            notify(job, f"🎬 Preview tập #{ep} (đã duyệt): {preview}", s)
        return {"resent_only": True, "episode_status": status}
    ap = request_approval("content", episode_id=ep, payload={"preview": str(preview)})
    set_stage(ep, "notify_preview", "awaiting_approval")
    chat = chat_for(job, s)
    size_mb = preview.stat().st_size / 1e6
    caption = (f"🎬 Preview tập #{ep} — {tl['duration_sec'] / 60:.1f} phút, {len(tl['scenes'])} cảnh.\n"
               f"Duyệt từng cảnh hoặc cả tập bên dưới. /approve {ep} để duyệt.")
    sent = "none"
    if chat:
        tg = TelegramClient(s)
        kb = scene_review_keyboard(ep, ap["id"])
        if size_mb <= s.telegram_max_upload_mb:
            tg.send_video(chat, preview, caption, kb)
            sent = "video"
        else:
            # Telegram bot upload limit: send a short teaser and the local path instead.
            teaser = preview.with_name("teaser_60s.mp4")
            media.ffmpeg(["-i", str(preview), "-t", "60", "-c", "copy", "-movflags", "+faststart", str(teaser)])
            tg.send_video(chat, teaser, caption + f"\n(File đầy đủ {size_mb:.0f}MB: {preview})", kb)
            sent = "teaser"
    return {"approval_id": ap["id"], "sent": sent, "size_mb": round(size_mb, 1)}


def h_render_final(job, s):
    ep = job["episode_id"]
    set_stage(ep, "render_final", "rendering_final")
    out = _final_path(s, ep)
    res = render.render_episode(ep, height=s.final_height, out_path=out, crf=20, settings=s)
    with connect() as c:
        c.execute("UPDATE episodes SET final_path=%s WHERE id=%s", (str(out), ep))
    return res


def h_render_tiktok(job, s):
    if not s.tiktok_enabled:
        return {"skipped": True}
    return shorts.render_short(job["episode_id"], settings=s)


def h_qc_tiktok(job, s):
    if not s.tiktok_enabled:
        return {"skipped": True}
    report = qc.check_short(job["episode_id"], settings=s)
    if not report["passed"]:
        raise QCFailed("; ".join(report["issues"]))
    audio_production.assert_publishable(job['episode_id'], s, shorts.short_paths(job['episode_id'], s)[0], short=True)
    return {"passed": True, "info": report["info"]}


def _passed_short(ep: int, s: Settings) -> Path | None:
    video, meta = shorts.short_paths(ep, s)
    report = meta.with_name("qc_report_tiktok.json")
    if s.tiktok_enabled and video.exists() and report.exists() and json.loads(report.read_text())["passed"]:
        try:
            audio_production.assert_publishable(ep, s, video, short=True)
            return video
        except AudioQCError:
            return None
    return None


def final_qc_passed(ep: int, s: Settings) -> bool:
    report = s.storage / "episodes" / str(ep) / f"qc_report_{s.final_height}p.json"
    if not (_final_path(s, ep).exists() and report.exists() and json.loads(report.read_text())["passed"]):
        return False
    try:
        audio_production.assert_publishable(ep, s, _final_path(s, ep))
        return True
    except AudioQCError:
        return False


def h_publish_queue(job, s):
    """Create publishing records awaiting approval. Never uploads automatically."""
    ep = job["episode_id"]
    if not final_qc_passed(ep, s):
        raise QCFailed(f"final video of episode {ep} has not passed QC; refusing to queue it for publishing")
    with connect() as c:
        e = c.execute("SELECT * FROM episodes WHERE id=%s", (ep,)).fetchone()
    ap = request_approval("publish", episode_id=ep, payload={"platform": "youtube"})
    attribution = audio_production.attribution_text(ep, s)
    with connect() as c:
        rec = c.execute("""INSERT INTO publishing_records (episode_id, platform, video_path, title, description, approval_id)
                           VALUES (%s,'youtube',%s,%s,%s,%s)
                           ON CONFLICT (episode_id, platform, video_path) DO UPDATE SET approval_id=EXCLUDED.approval_id,
                               status='awaiting_approval', last_error=NULL, updated_at=now()
                               WHERE publishing_records.status IN ('awaiting_approval','ready_manual','cancelled','failed') RETURNING *""",
                        (ep, e["final_path"], e["title"], f"{e['title']} — truyện audio Dark Fantasy" + attribution, ap["id"])).fetchone() \
            or c.execute("SELECT * FROM publishing_records WHERE episode_id=%s AND platform='youtube' AND video_path=%s",
                         (ep, e["final_path"])).fetchone()
        short = _passed_short(ep, s)
        tiktok = None
        if short:
            tiktok = c.execute("""INSERT INTO publishing_records (episode_id, platform, video_path, title, description,
                                                                  tags, approval_id)
                                  VALUES (%s,'tiktok',%s,%s,%s,%s,%s)
                                  ON CONFLICT (episode_id, platform, video_path) DO UPDATE SET approval_id=EXCLUDED.approval_id,
                               status='awaiting_approval', last_error=NULL, updated_at=now()
                               WHERE publishing_records.status IN ('awaiting_approval','ready_manual','cancelled','failed')
                                  RETURNING *""",
                               (ep, str(short), e["title"], f"{e['title']} — nghe trọn tập trên YouTube" + attribution,
                                ["truyenaudio", "darkfantasy", "truyenkinhdi"], ap["id"])).fetchone()
        for record in (rec, tiktok):
            if record and attribution and attribution not in (record.get('description') or ''):
                c.execute('UPDATE publishing_records SET description=COALESCE(description,\'\') || %s WHERE id=%s',
                          (attribution, record['id']))
    set_stage(ep, "publish_queue", "ready_to_publish")
    kb = {"inline_keyboard": [[{"text": "📤 Duyệt xuất bản", "callback_data": f"ap:{ap['id']}:y"},
                               {"text": "✋ Chưa", "callback_data": f"ap:{ap['id']}:n"}]]}
    text = f"✅ Bản 1080p tập #{ep} đã qua QC: {e['final_path']}"
    chat = chat_for(job, s)
    if short and chat and short.stat().st_size / 1e6 <= s.telegram_max_upload_mb:
        TelegramClient(s).send_video(chat, short, f"📱 Bản TikTok 9:16 tập #{ep} (đã qua QC).")
    elif short:
        text += f"\nTikTok 9:16: {short}"
    notify(job, text + "\nDuyệt đưa vào hàng đợi xuất bản?", s, kb)
    return {"publishing_record": rec["id"], "tiktok_record": tiktok["id"] if tiktok else None, "approval_id": ap["id"]}


def h_publish_upload(job, s):
    """Upload one approved publishing record. Refuses anything that was not approved by a human."""
    rid = (job.get("payload") or {}).get("record_id")
    with connect() as c:
        rec = c.execute("""SELECT r.*, a.status AS approval_status FROM publishing_records r
                           JOIN approvals a ON a.id = r.approval_id WHERE r.id=%s""", (rid,)).fetchone()
    if rec is None:
        raise UploadRefused(f"publishing record {rid} not found")
    if rec["external_id"]:
        return {"record_id": rid, "external_id": rec["external_id"], "already_uploaded": True}
    if rec["status"] == "uploading":
        with connect() as c:
            c.execute("""UPDATE publishing_records SET status='failed', updated_at=now(), last_error=%s WHERE id=%s""",
                      ("upload interrupted; check YouTube Studio / TikTok before re-queueing with /publish", rid))
        notify(job, f"⚠️ Upload bản ghi #{rid} bị gián đoạn. Kiểm tra YouTube Studio/TikTok xem video đã lên chưa, "
                    f"rồi /publish {rec['episode_id']} nếu cần đăng lại.", s)
        raise UploadRefused(f"record {rid} upload was interrupted; not retrying automatically")
    if rec["approval_status"] != "approved" or rec["status"] != "approved":
        raise UploadRefused(f"record {rid} is not approved for upload (status {rec['status']})")
    if not s.publish_upload_enabled:
        raise UploadRefused("PUBLISH_UPLOAD_ENABLED=false")
    try:
        audio_production.assert_publishable(rec['episode_id'], s, Path(rec['video_path']), short=rec['platform']=='tiktok')
    except AudioQCError as exc:
        raise UploadRefused(str(exc)) from exc
    with connect() as c:
        c.execute("UPDATE publishing_records SET status='uploading', updated_at=now() WHERE id=%s", (rid,))
    srt = s.storage / "episodes" / str(rec["episode_id"]) / "subtitles.srt"
    try:
        res = uploaders.upload_record(rec, s, srt=srt if rec["platform"] == "youtube" else None)
    except Exception as e:
        with connect() as c:
            c.execute("UPDATE publishing_records SET status='approved', last_error=%s, updated_at=now() WHERE id=%s",
                      (str(e)[-2000:], rid))
        raise
    status = "published" if rec["platform"] == "youtube" else "uploaded_draft"
    with connect() as c:
        c.execute("""UPDATE publishing_records SET status=%s, external_id=%s, external_url=%s, last_error=NULL,
                     published_at=now(), updated_at=now() WHERE id=%s""",
                  (status, res["external_id"], res.get("url"), rid))
    if rec["platform"] == "youtube":
        msg = f"📺 Đã upload YouTube ({res['privacy']}): {res['url']}"
    else:
        msg = "📱 Đã gửi video vào hộp nháp TikTok. Mở app TikTok để hoàn tất đăng."
    notify(job, msg, s)
    return {"record_id": rid, **res}


class UploadRefused(RuntimeError):
    retryable = False


def h_drive_import(job, s):
    from .drive import DriveError, fetch_story
    from .commands import story_text
    try:
        with httpx.Client(timeout=60) as client:
            path = fetch_story(job["payload"]["file_id"], s, client)
        imported = story.import_story(path)
    except (DriveError, story.StoryError) as exc:
        exc.retryable = False
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise RuntimeError("Không kết nối được Google Drive hoặc không ghi được file. Kiểm tra mạng và thử lại.") from exc
    sid = imported["story"]["id"]
    # Include the review in the result so /stories remains useful if sending fails.
    return {"story_id": sid, "created": imported["created"], "review": story_text(sid)}


def h_story_generate(job, s):
    """Local-model draft generation is asynchronous and still requires rights/content approval."""
    from .horror_story import generate, export_draft
    from .audio_engine.io import stable_hash
    from .commands import story_text
    payload=job.get('payload') or {}
    brief=payload['brief'];continuity=payload.get('continuity') or {}
    key=stable_hash([brief,continuity,s.story_engine.model_dump()])[:20]
    path=s.storage/'stories/generated'/f'{key}.json'
    if path.exists():
        result=json.loads(path.read_text())
    else:
        result=generate(brief,s.story_engine,continuity)
    source=export_draft(result,path,rights=brief.get('rights','unknown'))
    if result['quality']['status']=='FAIL':
        raise QCFailed('Generated draft failed story validation; retained for author review')
    imported=story.import_story(source);sid=imported['story']['id']
    return {'story_id':sid,'created':imported['created'],'review':story_text(sid),'provider':'local-only','paid_api':False}


HANDLERS = {
    'story_generate': h_story_generate,
    "drive_import": h_drive_import,
    "script_build": h_script_build,
    "tts": h_tts,
    "story_validate": h_story_validate,
    "scene_sound_analysis": h_scene_sound_analysis,
    "tts_qc": lambda job, s: audio_production.source_qc(job['episode_id'], s),
    "audio_repair": lambda job, s: audio_production.repair_narration(job['episode_id'], s),
    "narration_qc": lambda job, s: audio_production.narration_qc(job['episode_id'], s),
    "sound_assets": lambda job, s: audio_production.resolve_sounds(job['episode_id'], s),
    "sound_timeline": lambda job, s: audio_production.sound_timeline(job['episode_id'], s),
    "audio_mix": lambda job, s: audio_production.mix_episode(job['episode_id'], s),
    "audio_master": lambda job, s: audio_production.master_episode(job['episode_id'], s),
    "master_audio_qc": lambda job, s: audio_production.master_qc(job['episode_id'], s),
    "visual_plan": h_visual_plan,
    "visual_generate": h_visual_generate,
    "timeline": h_timeline,
    "render_preview": h_render_preview,
    "qc_preview": h_qc_preview,
    "notify_preview": h_notify_preview,
    "render_final": h_render_final,
    "qc_final": h_qc_final,
    "render_tiktok": h_render_tiktok,
    "qc_tiktok": h_qc_tiktok,
    "publish_queue": h_publish_queue,
    "publish_upload": h_publish_upload,
}


def run_job(job: dict, s: Settings | None = None) -> dict:
    s = s or get_settings()
    ep = job["episode_id"]
    handler = HANDLERS.get(job["type"])
    if handler is None:
        raise ValueError(f"no handler for {job['type']}")
    if ep and job["type"] != "publish_upload":  # uploads must not disturb the episode's ready_to_publish state
        set_stage(ep, job["type"], "running")
    log_event(f"stage started: {job['type']} (attempt {job['attempts']})", episode_id=ep, job_id=job["id"])
    result = handler(job, s)
    log_event(f"stage done: {job['type']}", episode_id=ep, job_id=job["id"], data=result)
    if ep and job["type"] not in ("notify_preview", "publish_queue", "publish_upload") \
            and not (isinstance(result, dict) and result.get("resent_only")):
        set_stage(ep, job["type"], "stage_done")
    return result


def after_success(job: dict, s: Settings) -> None:
    emit_event("stage.completed", job, s)
    if job["type"] in ("drive_import", "story_generate"):
        with connect() as c:
            row = c.execute("SELECT result FROM jobs WHERE id=%s", (job["id"],)).fetchone()
        if row and row["result"]:
            notify(job, row["result"]["review"], s)
        return
    if not s.auto_chain:
        return
    nxt = jobs.next_stage(job["type"])
    if nxt:
        payload = {k: v for k, v in (job.get("payload") or {}).items() if k != "run"}
        chain_from(nxt, job["episode_id"], payload, (job.get("payload") or {}).get("run") or f"j{job['id']}")


OPTIONAL_STAGES = ("render_tiktok", "qc_tiktok")


def after_failure(job: dict, error: str, final: bool, s: Settings) -> None:
    emit_event("stage.failed" if final else "stage.retry", job, s, {"error": error[-500:]})
    if final and job["type"] == "drive_import":
        notify(job, f"❌ Không nhập được tài liệu Drive (job #{job['id']}):\n{error[-500:]}\nXem /stories hoặc gửi lại /drive <link>.", s)
        return
    if final and job['type']=='story_generate':
        notify(job, '❌ Chưa tạo được kịch bản bằng mô hình local. Kiểm tra runtime local và xem lỗi job; không có API trả phí được gọi.', s)
        return
    if final and job["episode_id"]:
        if job["type"] in OPTIONAL_STAGES:
            notify(job, f"⚠️ Tập #{job['episode_id']}: bản TikTok lỗi ({error[-300:]}). Vẫn tiếp tục với bản YouTube.", s)
            if s.auto_chain:
                payload = {k: v for k, v in (job.get("payload") or {}).items() if k != "run"}
                chain_from("publish_queue", job["episode_id"], payload,
                           (job.get("payload") or {}).get("run") or f"j{job['id']}")
            return
        if job["type"] != "publish_upload":
            set_stage(job["episode_id"], job["type"], "needs_listening_review" if error.startswith('ListeningReviewRequired') else "failed", error[-2000:])
        notify(job, f"❌ Tập #{job['episode_id']} lỗi ở bước {job['type']}:\n{error[-600:]}", s)


def clear_regen_outputs(episode_id: int, s: Settings) -> None:
    """Remove derived reports so stale QC results are never mistaken for the new render."""
    d = s.storage / "episodes" / str(episode_id)
    for p in d.glob("qc_report_*.json"):
        p.unlink()
    shutil.rmtree(d / "generated" / "_tmp", ignore_errors=True)
