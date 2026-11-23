"""Telegram Controller: parse updates, enforce the user allowlist, dispatch commands.

Commands never execute shell; arguments are parsed as integers/whitelisted words only.
Heavy work is enqueued as jobs, so handling an update returns quickly.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from . import budget, jobs, pipeline, story
from .config import PROJECT_ROOT, Settings, get_settings
from .db import connect, log_event
from .telegram import TelegramClient, TelegramError
from .visual import engine

SAMPLE_STORY = PROJECT_ROOT / "samples" / "ngon-den-cuoi-cung-cua-vuc-tro.md"
HELP = """🌑 Audio Story Studio
/newstory — gửi file .md/.txt hoặc link Drive
/generate_story <ý tưởng> — tạo bản thảo bằng mô hình local
/drive <link> — tải truyện từ Google Drive
/stories — danh sách truyện và yêu cầu Drive gần đây
/story <story_id> — xem nội dung sẽ được đọc
/create_episode <story_id> — tạo tập & chạy pipeline
/status [episode_id] — trạng thái
/preview <episode_id> — gửi lại preview
/approve <episode_id> — duyệt preview → render 1080p
/approve rights <story_id> — duyệt bản quyền truyện
/regen_scene <episode_id> <scene_no> — tạo lại ảnh 1 cảnh
/budget — chi phí tháng này
/cancel <episode_id> — huỷ các job đang chờ
/publish <episode_id> — đưa bản final vào hàng đợi xuất bản (cần duyệt)
/xong — kết thúc khi dán truyện bằng tin nhắn"""

MAIN_MENU = {"inline_keyboard": [[{"text": "📊 Trạng thái", "callback_data": "menu:status"},
                                  {"text": "💰 Ngân sách", "callback_data": "menu:budget"}]]}


class CommandError(ValueError):
    pass


def _ints(args: list[str], n: int, usage: str) -> list[int]:
    if len(args) < n or not all(re.fullmatch(r"\d{1,9}", a) for a in args[:n]):
        raise CommandError(f"Cú pháp: {usage}")
    return [int(a) for a in args[:n]]


def _seen(update_id: int | None) -> bool:
    if update_id is None:
        return False
    with connect() as c:
        row = c.execute("INSERT INTO telegram_updates (update_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING update_id",
                        (update_id,)).fetchone()
    return row is None


def handle_update(update: dict, settings: Settings | None = None, tg: TelegramClient | None = None) -> dict:
    s = settings or get_settings()
    tg = tg or TelegramClient(s)
    if _seen(update.get("update_id")):
        return {"ok": True, "duplicate": True}
    if "callback_query" in update:
        cq = update["callback_query"]
        user_id, chat_id = cq["from"]["id"], cq["message"]["chat"]["id"]
        if user_id not in s.allowed_user_ids:
            log_event(f"telegram: rejected callback from unauthorised user {user_id}", level="warning")
            return {"ok": False, "reason": "unauthorized"}
        try:
            reply = handle_callback(cq.get("data", ""), user_id, chat_id, s)
        except (CommandError, pipeline.ApprovalError, ValueError) as e:
            reply = f"⚠️ {e}"
        tg.answer_callback(cq["id"], reply[:150])
        tg.send_message(chat_id, reply)
        return {"ok": True, "reply": reply}

    msg = update.get("message") or {}
    user_id = (msg.get("from") or {}).get("id")
    chat_id = (msg.get("chat") or {}).get("id")
    if user_id not in s.allowed_user_ids:
        log_event(f"telegram: rejected message from unauthorised user {user_id}", level="warning")
        return {"ok": False, "reason": "unauthorized"}
    text = (msg.get("text") or msg.get("caption") or "").strip()
    try:
        if msg.get("document"):
            reply = handle_document(msg["document"], user_id, chat_id, s, tg)
        elif text.startswith(("https://docs.google.com/", "https://drive.google.com/")):
            reply = queue_drive_import(text, user_id, chat_id)
        elif text and not text.startswith("/") and _pending(user_id) == "await_story_doc":
            reply = append_story_text(user_id, text, s)
        elif text.startswith("/"):
            reply, markup = handle_command(text, user_id, chat_id, s)
            tg.send_message(chat_id, reply, markup)
            return {"ok": True, "reply": reply}
        else:
            reply = "Gõ /start để xem các lệnh."
    except (CommandError, pipeline.ApprovalError, story.StoryError, ValueError, TelegramError) as e:
        reply = f"⚠️ {e}"
    tg.send_message(chat_id, reply)
    return {"ok": True, "reply": reply}


def handle_command(text: str, user_id: int, chat_id: int, s: Settings) -> tuple[str, dict | None]:
    parts = text.split()
    cmd = parts[0].split("@")[0].lower()
    args = parts[1:]

    if cmd == "/start":
        return HELP, MAIN_MENU
    if cmd == '/generate_story':
        idea=' '.join(args)
        if not idea or len(idea)>4000:
            raise CommandError('Cú pháp: /generate_story <ý tưởng truyện, tối đa 4000 ký tự>')
        from .audio_engine.io import stable_hash
        key=stable_hash([user_id,idea])[:20]
        job=jobs.enqueue('story_generate',None,payload={'brief':{'premise':idea},'chat_id':chat_id,'user_id':user_id},
                         idempotency_key=f'story_generate:{key}',max_attempts=2)
        if job['status']=='succeeded' and (job.get('result') or {}).get('review'):
            return job['result']['review'],None
        if job['status'] in ('failed','cancelled'):
            job=jobs.enqueue('story_generate',None,payload={'brief':{'premise':idea},'chat_id':chat_id,'user_id':user_id},
                             idempotency_key=f'story_generate:{key}:retry-{time.time_ns()}',max_attempts=2)
        return f'📝 Đã xếp job #{job["id"]} tạo bản thảo bằng mô hình local. Chưa tạo audio hay tự duyệt bản quyền.',None
    if cmd == "/drive":
        if len(args) != 1:
            raise CommandError("Cú pháp: /drive <link Google Docs hoặc Drive>")
        return queue_drive_import(args[0], user_id, chat_id), None
    if cmd == "/stories":
        return stories_text(user_id), None
    if cmd == "/story":
        (sid,) = _ints(args, 1, "/story <story_id>")
        return story_text(sid), None
    if cmd == "/newstory":
        if args and args[0].startswith(("https://docs.google.com/", "https://drive.google.com/")):
            return queue_drive_import(args[0], user_id, chat_id), None
        if args and args[0] == "sample":
            return _import_and_ask_rights(SAMPLE_STORY), None
        with connect() as c:
            c.execute("""INSERT INTO telegram_state (user_id, pending) VALUES (%s,'await_story_doc')
                         ON CONFLICT (user_id) DO UPDATE SET pending='await_story_doc', updated_at=now()""", (user_id,))
        _draft_path(user_id, s).unlink(missing_ok=True)
        return ("📄 Gửi link Google Docs/Drive, file truyện (.md/.txt, ≤2MB) — hoặc dán nội dung truyện thành nhiều tin nhắn liên tiếp "
                "rồi gõ /xong.\nDòng đầu là tên truyện; chương bắt đầu bằng '## Tên chương'."), None
    if cmd in ("/xong", "/done"):
        return finish_story_text(user_id, s), None
    if cmd == "/create_episode":
        (story_id,) = _ints(args, 1, "/create_episode <story_id>")
        eps = story.create_episodes(story_id)
        started = []
        for ep in eps:
            if ep["status"] in ("created", "failed", "cancelled"):
                pipeline.start_episode_pipeline(ep["id"], chat_id=chat_id, run=f"t{int(time.time())}")
                started.append(ep["id"])
        return (f"🎬 Truyện #{story_id}: {len(eps)} tập. Đã bắt đầu pipeline cho tập {started or 'không có (đang chạy/đã xong)'}."
                f" Theo dõi: /status {eps[0]['id']}"), None
    if cmd == "/status":
        if not args:
            return status_text(None) + "\n\n" + drive_jobs_text(user_id), None
        return status_text(int(args[0]) if args[0].isdigit() else None), None
    if cmd == "/preview":
        (ep,) = _ints(args, 1, "/preview <episode_id>")
        job = jobs.enqueue("notify_preview", ep, payload={"chat_id": chat_id, "resend": True},
                           idempotency_key=f"notify_preview:{ep}:manual{int(time.time())}")
        return f"📨 Sẽ gửi preview tập #{ep} (job {job['id']}).", None
    if cmd == "/approve":
        if args and args[0] == "rights":
            (sid,) = _ints(args[1:], 1, "/approve rights <story_id>")
            ap = pipeline.request_approval("rights", story_id=sid)
            pipeline.decide(ap["id"], True, user_id, chat_id=chat_id)
            return f"✅ Đã duyệt bản quyền truyện #{sid}. Tiếp theo: /create_episode {sid}", None
        (ep,) = _ints(args, 1, "/approve <episode_id>")
        with connect() as c:
            ap = c.execute("SELECT id FROM approvals WHERE kind='content' AND episode_id=%s AND status='pending'",
                           (ep,)).fetchone()
        if not ap:
            raise CommandError(f"Tập #{ep} không có preview nào đang chờ duyệt.")
        pipeline.decide(ap["id"], True, user_id, chat_id=chat_id)
        return f"✅ Đã duyệt preview tập #{ep}. Đang render 1080p.", None
    if cmd == "/regen_scene":
        ep, scene_no = _ints(args, 2, "/regen_scene <episode_id> <scene_no>")
        return regen_scene(ep, scene_no, chat_id, s), None
    if cmd == "/budget":
        return budget_text(s), None
    if cmd == "/cancel":
        (ep,) = _ints(args, 1, "/cancel <episode_id>")
        n = jobs.cancel_episode_jobs(ep)
        pipeline.set_stage(ep, "cancelled", "cancelled")
        return f"🛑 Đã huỷ {n} job đang chờ của tập #{ep} (bước đang chạy sẽ dừng lại sau khi xong, không chạy tiếp).", None
    if cmd == "/publish":
        (ep,) = _ints(args, 1, "/publish <episode_id>")
        if not pipeline.final_qc_passed(ep, s):
            raise CommandError(f"Tập #{ep} chưa có bản final đã qua QC.")
        job = jobs.enqueue("publish_queue", ep, payload={"chat_id": chat_id},
                           idempotency_key=f"publish_queue:{ep}:manual{int(time.time())}")
        return f"📤 Đang tạo yêu cầu xuất bản tập #{ep} (job {job['id']}). Sẽ cần duyệt.", None
    raise CommandError("Lệnh không hợp lệ. Gõ /start.")


def handle_callback(data: str, user_id: int, chat_id: int, s: Settings) -> str:
    if data == "menu:status":
        return status_text(None)
    if data == "menu:budget":
        return budget_text(s)
    m = re.fullmatch(r"ap:(\d+):([yn])", data)
    if m:
        res = pipeline.decide(int(m.group(1)), m.group(2) == "y", user_id, chat_id=chat_id)
        ap = res["approval"]
        if not res["changed"]:
            return f"Yêu cầu #{ap['id']} đã được xử lý trước đó ({ap['status']})."
        return f"{'✅ Đã duyệt' if ap['status'] == 'approved' else '❌ Đã từ chối'} ({ap['kind']} #{ap['id']})."
    m = re.fullmatch(r"sc:(\d+):(ok|regen)", data)
    if m:
        scene_id = int(m.group(1))
        with connect() as c:
            sc = c.execute("SELECT id, episode_id, scene_no FROM scenes WHERE id=%s", (scene_id,)).fetchone()
        if not sc:
            raise CommandError("Cảnh không tồn tại.")
        if m.group(2) == "ok":
            ap = pipeline.request_approval("scene", episode_id=sc["episode_id"], scene_id=scene_id)
            pipeline.decide(ap["id"], True, user_id, chat_id=chat_id)
            return f"✅ Đã duyệt cảnh {sc['scene_no']} (tập #{sc['episode_id']})."
        return regen_scene(sc["episode_id"], sc["scene_no"], chat_id, s)
    raise CommandError("Nút không hợp lệ.")


def regen_scene(ep: int, scene_no: int, chat_id, s: Settings) -> str:
    info = engine.request_scene_regen(ep, scene_no, settings=s)
    pipeline.clear_regen_outputs(ep, s)
    run = f"regen{scene_no}-{info['request_id']}"
    jobs.enqueue("visual_generate", ep, payload={"chat_id": chat_id, "run": run},
                 idempotency_key=f"visual_generate:{ep}:{run}")
    return (f"🔁 Đang tạo lại ảnh cảnh {scene_no} của tập #{ep}. Chỉ cảnh này được render lại;"
            " preview mới sẽ được gửi khi xong.")


MAX_DRAFT_CHARS = 400_000


def _pending(user_id: int) -> str | None:
    with connect() as c:
        st = c.execute("SELECT pending FROM telegram_state WHERE user_id=%s", (user_id,)).fetchone()
    return st["pending"] if st else None


def _draft_path(user_id: int, s: Settings) -> Path:
    return s.storage / "stories" / "_inbox" / f"draft_{int(user_id)}.md"


def append_story_text(user_id: int, text: str, s: Settings) -> str:
    """Story pasted from a phone: each message is appended to the user's draft until /xong."""
    draft = _draft_path(user_id, s)
    draft.parent.mkdir(parents=True, exist_ok=True)
    current = draft.read_text(encoding="utf-8") if draft.exists() else ""
    if len(current) + len(text) > MAX_DRAFT_CHARS:
        raise CommandError(f"Truyện quá dài (> {MAX_DRAFT_CHARS:,} ký tự).")
    draft.write_text(current + ("\n\n" if current else "") + text.strip(), encoding="utf-8")
    return f"✍️ Đã nhận ({len(current) + len(text):,} ký tự). Gửi tiếp hoặc /xong để nhập truyện."


def finish_story_text(user_id: int, s: Settings) -> str:
    draft = _draft_path(user_id, s)
    if _pending(user_id) != "await_story_doc" or not draft.exists():
        raise CommandError("Chưa có nội dung. Gõ /newstory rồi dán truyện.")
    body = draft.read_text(encoding="utf-8").strip()
    if len(body) < 200:
        raise CommandError("Truyện quá ngắn (cần ≥ 200 ký tự).")
    if not body.startswith("---") and not body.startswith("#"):
        title, _, rest = body.partition("\n")
        body = f"# {title.strip()}\n\n{rest.strip()}"
    dest = draft.with_name(f"{int(time.time())}_{user_id}.md")
    dest.write_text(body + "\n", encoding="utf-8")
    draft.unlink()
    with connect() as c:
        c.execute("UPDATE telegram_state SET pending=NULL WHERE user_id=%s", (user_id,))
    return _import_and_ask_rights(dest)


def handle_document(doc: dict, user_id: int, chat_id: int, s: Settings, tg: TelegramClient) -> str:
    with connect() as c:
        st = c.execute("SELECT pending FROM telegram_state WHERE user_id=%s", (user_id,)).fetchone()
    if not st or st["pending"] != "await_story_doc":
        raise CommandError("Hãy gõ /newstory trước khi gửi file truyện.")
    name = doc.get("file_name") or "story.md"
    if not re.fullmatch(r"[\w.\- ]{1,100}\.(md|txt)", name, flags=re.UNICODE):
        raise CommandError("Chỉ nhận file .md hoặc .txt với tên đơn giản.")
    inbox = s.storage / "stories" / "_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    dest = inbox / f"{int(time.time())}_{story.slugify(Path(name).stem)}.md"
    tg.download_document(doc["file_id"], dest)
    with connect() as c:
        c.execute("UPDATE telegram_state SET pending=NULL WHERE user_id=%s", (user_id,))
    return _import_and_ask_rights(dest)


def _import_and_ask_rights(path: Path) -> str:
    res = story.import_story(path)
    st = res["story"]
    if st["rights_status"] == "approved":
        return f"📚 Truyện #{st['id']} '{st['title']}' đã có và đã duyệt bản quyền. /create_episode {st['id']}"
    return (f"📚 Đã nhập truyện #{st['id']} '{st['title']}' (rights: {st['rights_type']}).\n"
            f"Xác nhận bạn có quyền sử dụng nội dung này: /approve rights {st['id']}")


def status_text(episode_id: int | None) -> str:
    with connect() as c:
        if episode_id is None:
            rows = c.execute("""SELECT id, title, status, current_stage FROM episodes ORDER BY updated_at DESC LIMIT 8""").fetchall()
            if not rows:
                return "Chưa có tập nào. /newstory để bắt đầu."
            return "📊 Các tập gần đây:\n" + "\n".join(
                f"#{r['id']} {r['title'][:40]} — {r['status']} ({r['current_stage'] or '-'})" for r in rows)
        e = c.execute("SELECT * FROM episodes WHERE id=%s", (episode_id,)).fetchone()
        if not e:
            return f"Không tìm thấy tập #{episode_id}."
        js = c.execute("""SELECT type, status, attempts FROM jobs WHERE episode_id=%s ORDER BY id DESC LIMIT 8""",
                       (episode_id,)).fetchall()
        ev = c.execute("""SELECT level, message, created_at FROM job_events WHERE episode_id=%s ORDER BY id DESC LIMIT 5""",
                       (episode_id,)).fetchall()
    lines = [f"📊 Tập #{e['id']} — {e['title']}", f"Trạng thái: {e['status']} / bước: {e['current_stage']}"]
    if e["narration_duration_sec"]:
        lines.append(f"Thời lượng: {float(e['narration_duration_sec']) / 60:.1f} phút")
    if e["last_error"]:
        lines.append(f"Lỗi: {e['last_error'][:300]}")
    lines.append("Jobs: " + ", ".join(f"{j['type']}={j['status']}" for j in js))
    lines += [f"• {x['created_at']:%H:%M:%S} {x['message'][:120]}" for x in ev]
    return "\n".join(lines)


def budget_text(s: Settings) -> str:
    b = budget.summary(s)
    lines = [f"💰 Ngân sách {b['period']}: đã dùng/giữ {b['spent_or_reserved_vnd']:,} / {b['monthly_budget_vnd']:,} VND",
             f"API trả phí: {'BẬT' if b['allow_paid_api'] else 'TẮT'}"]
    for l in b["lines"]:
        lines.append(f"• {l['category']}/{l['provider']} {l['status']}: {int(l['n'])} lần, {int(l['est'] or 0):,} VND")
    return "\n".join(lines)


def queue_drive_import(source: str, user_id: int, chat_id: int) -> str:
    from .drive import file_id
    fid = file_id(source)
    with connect() as c:
        pending = c.execute("""SELECT id FROM jobs WHERE type='drive_import' AND status IN ('queued','running')
                               AND payload->>'user_id'=%s AND payload->>'file_id'=%s ORDER BY id DESC LIMIT 1""",
                            (str(user_id), fid)).fetchone()
        if pending:
            return f"⏳ Đang xử lý tài liệu này (job #{pending['id']}). Xem /stories."
        job = jobs.enqueue("drive_import", None,
                           payload={"file_id": fid, "user_id": user_id, "chat_id": chat_id},
                           idempotency_key=f"drive_import:{user_id}:{fid}:{time.time_ns()}", conn=c)
        c.execute("UPDATE telegram_state SET pending=NULL WHERE user_id=%s", (user_id,))
    return f"📥 Đã nhận link Drive (job #{job['id']}). Bot sẽ tải và báo phần lời kể để bạn kiểm tra. Theo dõi /stories."


def drive_jobs_text(user_id: int) -> str:
    with connect() as c:
        rows = c.execute("""SELECT id, status, last_error, result FROM jobs WHERE type='drive_import'
                            AND payload->>'user_id'=%s ORDER BY id DESC LIMIT 3""", (str(user_id),)).fetchall()
    if not rows:
        return "Nhập từ Drive: /drive <link>"
    lines = ["📥 Yêu cầu Drive gần đây:"]
    for r in rows:
        detail = ""
        if r["status"] == "failed":
            detail = " — " + (r["last_error"] or "")[:180]
        elif r["result"] and r["result"].get("story_id"):
            detail = f" — /story {r['result']['story_id']}"
        lines.append(f"Job #{r['id']}: {r['status']}{detail}")
    return "\n".join(lines)


def stories_text(user_id: int) -> str:
    with connect() as c:
        rows = c.execute("SELECT id, title, rights_status FROM stories ORDER BY id DESC LIMIT 8").fetchall()
    lines = ["📚 Truyện gần đây:"]
    lines += [f"#{r['id']} {r['title'][:90]} — {r['rights_status']}\n/story {r['id']}" for r in rows]
    if not rows:
        lines.append("Chưa có truyện. Gửi /drive <link> để bắt đầu.")
    return "\n".join(lines) + "\n\n" + drive_jobs_text(user_id)


def story_text(story_id: int) -> str:
    with connect() as c:
        st = c.execute("SELECT * FROM stories WHERE id=%s", (story_id,)).fetchone()
        if not st:
            raise CommandError(f"Không tìm thấy truyện #{story_id}.")
        chapters = c.execute("SELECT title, char_count, content FROM chapters WHERE story_id=%s ORDER BY chapter_no",
                             (story_id,)).fetchall()
    chars = sum(ch["char_count"] for ch in chapters)
    lines = [f"📚 Truyện #{story_id}: {st['title']}",
             f"Phần lời kể: {len(chapters)} mục, {chars:,} ký tự.",
             f"Ước tính {chars / story.EST_CHARS_PER_SEC / 60:.1f} phút; cần đo lại sau TTS."]
    intake = (st.get("metadata") or {}).get("intake") or {}
    if intake.get("kind") == "story_bible":
        lines.append("Đã tách phần kịch bản; không đọc giới thiệu, bí mật mùa, danh sách tập và ghi chú biên tập.")
    lines += ["• " + ch["title"][:90] for ch in chapters[:12]]
    if chapters:
        lines.append("\nĐoạn mở đầu sẽ đọc:\n" + chapters[0]["content"][:400])
    lines.append(f"\nBản quyền: {st['rights_status']}.")
    if st["rights_status"] == "approved":
        lines.append(f"Tạo audio/video preview: /create_episode {story_id}")
    else:
        lines.append(f"Kiểm tra nội dung và quyền sử dụng rồi duyệt: /approve rights {story_id}")
    return "\n".join(lines)
