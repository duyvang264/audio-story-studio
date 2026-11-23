"""Command-line entry points: migrate, environment checks, story intake, end-to-end smoke test."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def cmd_migrate(_):
    from .db import migrate
    from .config import ensure_storage
    ensure_storage()
    print("applied:", migrate() or "nothing (up to date)")


def cmd_check_env(_):
    checks = {}
    for tool in ("ffmpeg", "ffprobe", "say", "codex", "docker", "psql"):
        checks[tool] = shutil.which(tool) is not None
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=20).stdout
        checks["vi_voice_Linh"] = "Linh" in out and "vi_VN" in out
    except (OSError, subprocess.TimeoutExpired):
        checks["vi_voice_Linh"] = False
    try:
        filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        for f in ("zoompan", "xfade", "loudnorm", "silencedetect", "blackdetect", "subtitles", "drawtext"):
            checks[f"ffmpeg_{f}"] = f" {f} " in filters
    except OSError:
        pass
    from .config import get_settings
    s = get_settings()
    try:
        import piper  # noqa: F401
        checks["piper_tts"] = True
    except ImportError:
        checks["piper_tts"] = False
    model = s.project_path(s.piper_model)
    checks["piper_vi_model"] = bool(model and model.exists())
    from .subtitles import FONT_CANDIDATES
    checks["subtitle_font"] = next((f for f in FONT_CANDIDATES if Path(f).exists()), False)
    bgm = s.project_path(s.bgm_path)
    checks["bgm_file"] = bool(bgm and bgm.exists())
    checks["telegram"] = telegram_checks(s)
    from . import uploaders
    checks["publish_upload_enabled"] = s.publish_upload_enabled
    yt_secrets = s.project_path(s.youtube_client_secrets)
    checks["youtube_client_secrets"] = bool(yt_secrets and yt_secrets.exists())
    checks["youtube_token"] = uploaders.load_token("youtube", s) is not None
    checks["tiktok_app"] = bool(s.tiktok_client_key and s.tiktok_client_secret and s.tiktok_redirect_uri)
    checks["tiktok_token"] = uploaders.load_token("tiktok", s) is not None
    try:
        out = subprocess.run(["codex", "login", "status"], capture_output=True, text=True, timeout=20)
        checks["codex_login"] = (out.stdout + out.stderr).strip()
    except (OSError, subprocess.TimeoutExpired):
        checks["codex_login"] = None
    try:
        r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=20)
        checks["docker_daemon"] = r.stdout.strip() or False
    except (OSError, subprocess.TimeoutExpired):
        checks["docker_daemon"] = False
    try:
        from .db import fetchone
        fetchone("SELECT 1")
        checks["postgres"] = True
    except Exception as e:  # noqa: BLE001
        checks["postgres"] = str(e)[:120]
    print(json.dumps(checks, indent=2, ensure_ascii=False))


def cmd_check_codex(args):
    """Generate ONE image through Codex built-in image_gen to verify headless support."""
    os.environ["CODEX_IMAGEGEN_VERIFIED"] = "true"
    from .config import get_settings
    from .visual.providers import CodexImageProvider
    s = get_settings()
    out = Path(args.out).resolve()
    t = time.time()
    meta = CodexImageProvider(s).generate(
        "dark fantasy cinematic, ruined temple in misty mountains at dusk, 16:9", "text, watermark", out)
    from PIL import Image
    with Image.open(out) as im:
        print(json.dumps({"ok": True, "path": str(out), "size": im.size, "seconds": round(time.time() - t, 1),
                          **meta}))
    print("Set CODEX_IMAGEGEN_VERIFIED=true and IMAGE_PROVIDER=codex in .env to use it.")


def cmd_make_bgm(args):
    from .bgm import generate_dark_ambient
    from .config import get_settings
    s = get_settings()
    out = Path(args.out) if args.out else s.project_path(s.bgm_path)
    generate_dark_ambient(out, seconds=args.seconds)
    from . import media
    print(json.dumps({"path": str(out), "duration_sec": round(media.duration(out), 1),
                      "lufs": media.integrated_loudness(out)}))


def cmd_import(args):
    from .story import import_story
    res = import_story(Path(args.path))
    st = res["story"]
    print(json.dumps({"story_id": st["id"], "created": res["created"], "rights_status": st["rights_status"]}))


def cmd_smoke(args):
    """End-to-end: Telegram commands (dry-run) -> pipeline -> preview -> regen 1 scene -> approve -> final."""
    test_user = 999000001
    os.environ["TELEGRAM_DRY_RUN"] = "true"
    allowed = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")
    os.environ["TELEGRAM_ALLOWED_USER_IDS"] = f"{allowed},{test_user}".strip(",")
    os.environ.setdefault("TELEGRAM_NOTIFY_CHAT_ID", str(test_user))
    if args.image_provider:
        os.environ["IMAGE_PROVIDER"] = args.image_provider

    from . import commands, worker
    from .config import ensure_storage, get_settings
    from .db import connect, migrate

    s = get_settings()
    ensure_storage(s)
    migrate()
    outbox = s.storage / "logs" / "telegram_outbox.jsonl"
    outbox_start = outbox.stat().st_size if outbox.exists() else 0
    uid = [int(time.time() * 1000)]

    def tg(text):
        uid[0] += 1
        res = commands.handle_update({"update_id": uid[0], "message": {
            "from": {"id": test_user}, "chat": {"id": test_user}, "text": text}}, s)
        print(f"> {text}\n  {res.get('reply', res)}")
        return res

    def drain():
        t = time.time()
        while worker.run_one(s):
            pass
        return round(time.time() - t, 1)

    report: dict = {"image_provider": s.image_provider, "tts_provider": s.tts_provider}
    tg("/start")
    tg("/newstory sample")
    with connect() as c:
        story = c.execute("SELECT * FROM stories WHERE source_path LIKE %s ORDER BY id DESC LIMIT 1",
                          ("%ngon-den-cuoi-cung-cua-vuc-tro.md",)).fetchone()
        if args.fresh:
            c.execute("DELETE FROM episodes WHERE story_id=%s", (story["id"],))
    tg(f"/approve rights {story['id']}")
    tg(f"/create_episode {story['id']}")
    report["pipeline_seconds"] = drain()
    with connect() as c:
        ep = c.execute("SELECT * FROM episodes WHERE story_id=%s ORDER BY episode_no LIMIT 1", (story["id"],)).fetchone()
        scenes = c.execute("SELECT scene_no, start_sec, end_sec, location_id, characters, mood, motion, match_type "
                           "FROM scenes WHERE episode_id=%s ORDER BY scene_no", (ep["id"],)).fetchall()
        failed = c.execute("SELECT type, last_error FROM jobs WHERE episode_id=%s AND status='failed'", (ep["id"],)).fetchall()
    tg(f"/status {ep['id']}")
    report["episode_id"] = ep["id"]
    report["episode_status"] = ep["status"]
    report["failed_jobs"] = failed
    report["narration_min"] = round(float(ep["narration_duration_sec"] or 0) / 60, 2)
    report["scenes"] = [{**sc, "start_sec": float(sc["start_sec"]), "end_sec": float(sc["end_sec"]),
                         "dur": round(float(sc["end_sec"] - sc["start_sec"]), 1)} for sc in scenes]
    if ep["status"] != "awaiting_approval":
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        sys.exit("SMOKE FAILED: preview stage not reached")
    d = s.storage / "episodes" / str(ep["id"])
    report["preview"] = ep["preview_path"]
    report["qc_preview"] = json.loads((d / f"qc_report_{s.preview_height}p.json").read_text())

    regen_ok = True
    if not args.skip_regen and len(scenes) > 1:
        target = scenes[len(scenes) // 2]["scene_no"]
        with connect() as c:
            last_render = c.execute("SELECT COALESCE(MAX(id),0) AS id FROM jobs WHERE episode_id=%s "
                                    "AND type='render_preview'", (ep["id"],)).fetchone()["id"]
        tg(f"/regen_scene {ep['id']} {target}")
        drain()
        with connect() as c:
            j = c.execute("""SELECT result FROM jobs WHERE episode_id=%s AND type='render_preview' AND status='succeeded'
                             AND id > %s ORDER BY id DESC LIMIT 1""", (ep["id"], last_render)).fetchone()
            e2 = c.execute("SELECT status FROM episodes WHERE id=%s", (ep["id"],)).fetchone()
        render = j["result"] if j else None
        regen_ok = bool(render) and render["clips_reused"] == len(scenes) - 1 and e2["status"] == "awaiting_approval"
        report["regen"] = {"scene_no": target, "render": render, "passed": regen_ok}

    if not args.skip_final:
        tg(f"/approve {ep['id']}")
        drain()
        with connect() as c:
            ep = c.execute("SELECT * FROM episodes WHERE id=%s", (ep["id"],)).fetchone()
            pub = c.execute("SELECT id, platform, status FROM publishing_records WHERE episode_id=%s", (ep["id"],)).fetchall()
        report["final"] = ep["final_path"]
        report["final_status"] = ep["status"]
        report["qc_final"] = json.loads((d / f"qc_report_{s.final_height}p.json").read_text()) \
            if (d / f"qc_report_{s.final_height}p.json").exists() else None
        report["publishing_records"] = pub
        if s.tiktok_enabled:
            qt = d / "qc_report_tiktok.json"
            report["qc_tiktok"] = json.loads(qt.read_text()) if qt.exists() else None
            report["tiktok"] = json.loads((d / "tiktok.json").read_text()) if (d / "tiktok.json").exists() else None

    with open(outbox, encoding="utf-8") as f:
        f.seek(outbox_start)
        sent = [json.loads(line) for line in f if line.strip()]
    report["telegram_outbox"] = {m: sum(1 for x in sent if x["method"] == m) for m in {x["method"] for x in sent}}
    report["outputs"] = sorted(p.name for p in d.glob("*.json")) + ["subtitles.srt"]
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    ok = report["qc_preview"]["passed"] and regen_ok and (
        args.skip_final or (bool((report.get("qc_final") or {}).get("passed"))
                            and (not s.tiktok_enabled or bool((report.get("qc_tiktok") or {}).get("passed")))))
    print("SMOKE PASSED" if ok else "SMOKE FAILED")
    sys.exit(0 if ok else 1)


PLACEHOLDER_IDS = {123456789}


def telegram_checks(s) -> dict:
    """Validate Telegram settings by calling getMe/getWebhookInfo. Never prints the token."""
    import httpx

    from .telegram import API
    ids = s.allowed_user_ids
    out: dict = {"mode": s.telegram_mode, "dry_run": s.telegram_dry_run,
                 "token_set": bool(s.telegram_bot_token), "allowed_user_ids": sorted(ids),
                 "notify_chat_id": s.telegram_notify_chat_id or None, "problems": []}
    if not ids:
        out["problems"].append("TELEGRAM_ALLOWED_USER_IDS trống")
    if ids & PLACEHOLDER_IDS or s.telegram_notify_chat_id.strip() in {str(i) for i in PLACEHOLDER_IDS}:
        out["problems"].append("đang dùng số ví dụ 123456789 — thay bằng User ID thật (@userinfobot)")
    if s.telegram_notify_chat_id and s.telegram_notify_chat_id.strip().lstrip("-").isdigit() \
            and int(s.telegram_notify_chat_id) not in ids and not s.telegram_notify_chat_id.startswith("-"):
        out["problems"].append("TELEGRAM_NOTIFY_CHAT_ID khác User ID trong danh sách cho phép")
    if s.telegram_dry_run:
        out["problems"].append("TELEGRAM_DRY_RUN=true — bot sẽ không gửi tin thật")
    if not s.telegram_bot_token:
        out["problems"].append("TELEGRAM_BOT_TOKEN trống (lấy từ @BotFather)")
        return out
    base = f"{API}/bot{s.telegram_bot_token}"
    try:
        me = httpx.get(f"{base}/getMe", timeout=15).json()
        if me.get("ok"):
            out["bot"] = "@" + me["result"]["username"]
        else:
            out["problems"].append(f"token bị Telegram từ chối: {me.get('description')}")
            return out
        hook = httpx.get(f"{base}/getWebhookInfo", timeout=15).json().get("result") or {}
        out["webhook_set"] = bool(hook.get("url"))
        if hook.get("url") and s.telegram_mode == "polling":
            out["problems"].append("bot đang gắn webhook nên polling sẽ lỗi — gọi deleteWebhook")
        if ids and s.telegram_mode == "polling" and not hook.get("url"):
            # did an allowed user already press Start? (peek only; offset is not advanced)
            upd = httpx.get(f"{base}/getUpdates", params={"limit": 100, "timeout": 0}, timeout=15).json()
            senders = {((u.get("message") or u.get("callback_query") or {}).get("from") or {}).get("id")
                       for u in upd.get("result", [])}
            senders.discard(None)
            out["pending_updates_from"] = sorted(senders)
            unknown = senders - ids
            if unknown:
                out["problems"].append(f"có người nhắn bot nhưng không trong danh sách cho phép: {sorted(unknown)}")
    except httpx.HTTPError as e:
        out["problems"].append(f"không kết nối được Telegram: {type(e).__name__}")
    return out


def cmd_youtube_auth(args):
    """OAuth (Desktop app client, PKCE) with a one-shot loopback listener on 127.0.0.1."""
    import http.server
    import secrets
    import urllib.parse
    import webbrowser

    import httpx

    from . import uploaders
    from .config import get_settings

    s = get_settings()
    verifier, challenge = uploaders.pkce_pair()
    state = secrets.token_urlsafe(16)
    got: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if q.get("state") == state:
                got.update(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Đã nhận mã. Quay lại terminal.".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    url = uploaders.youtube_auth_url(s, redirect, state, challenge)
    print("Mở URL sau để cấp quyền upload YouTube:\n" + url)
    webbrowser.open(url)
    srv.timeout = 10
    deadline = time.time() + 300
    while "code" not in got and "error" not in got and time.time() < deadline:
        srv.handle_request()  # ignores stray requests (favicon…) whose state does not match
    srv.server_close()
    if "code" not in got:
        sys.exit(f"Không nhận được mã: {got.get('error', 'timeout')}")
    with httpx.Client(timeout=30) as client:
        uploaders.youtube_exchange(s, got["code"], redirect, verifier, client)
    print(f"OK: token lưu tại {uploaders.token_path('youtube', s)} (chmod 600)")


def cmd_tiktok_auth(args):
    """TikTok Login Kit (PKCE). TikTok requires a registered redirect URI, so the code is pasted back."""
    import secrets
    import urllib.parse

    import httpx

    from . import uploaders
    from .config import get_settings

    s = get_settings()
    if not (s.tiktok_client_key and s.tiktok_client_secret and s.tiktok_redirect_uri):
        sys.exit("Cần TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REDIRECT_URI trong .env")
    verifier, challenge = uploaders.pkce_pair()
    state = secrets.token_urlsafe(16)
    print("Mở URL sau, đăng nhập TikTok và cấp quyền:\n" + uploaders.tiktok_auth_url(s, state, challenge))
    pasted = input("Dán toàn bộ URL bị chuyển hướng tới (hoặc chỉ mã code): ").strip()
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(pasted).query)) if "://" in pasted else {"code": pasted}
    if "state" in q and q["state"] != state:
        sys.exit("state không khớp — huỷ")
    code = urllib.parse.unquote(q.get("code", ""))
    if not code:
        sys.exit("Không tìm thấy code")
    with httpx.Client(timeout=30) as client:
        uploaders.tiktok_exchange(s, code, verifier, client)
    print(f"OK: token lưu tại {uploaders.token_path('tiktok', s)} (chmod 600)")


def cmd_drive_auth(args):
    from .drive import authorize
    from .config import get_settings
    authorize(get_settings(), no_browser=args.no_browser)


def cmd_drive_import(args):
    import httpx
    from .drive import fetch_story
    from .config import get_settings
    from .story import import_story, parse_story_file
    with httpx.Client(timeout=60) as client:
        path = fetch_story(args.source, get_settings(), client)
    parsed = parse_story_file(path)
    result = {"path": str(path), "title": parsed["meta"]["title"],
              "chapters": len(parsed["chapters"]),
              "characters": sum(len(c["content"]) for c in parsed["chapters"])}
    if not args.download_only:
        imported = import_story(path)
        story = imported["story"]
        result.update(story_id=story["id"], created=imported["created"], rights_status=story["rights_status"])
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="studio")
    sub = ap.add_subparsers(dest="cmd", required=True)
    from .audio_engine.cli import register
    register(sub)
    p = sub.add_parser("drive-auth", help="authorise read-only Google Drive access")
    p.add_argument("--no-browser", action="store_true", help="print login URL without opening browser")
    p.set_defaults(fn=cmd_drive_auth)
    p = sub.add_parser("drive-import", help="download and import a Drive story URL or file ID")
    p.add_argument("source")
    p.add_argument("--download-only", action="store_true", help="download and inspect without database import")
    p.set_defaults(fn=cmd_drive_import)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    sub.add_parser("check-env").set_defaults(fn=cmd_check_env)
    p = sub.add_parser("check-codex-imagegen")
    p.add_argument("--out", default="storage/logs/codex_imagegen_check.png")
    p.set_defaults(fn=cmd_check_codex)
    p = sub.add_parser("make-bgm", help="generate the default dark-ambient BGM (royalty-free, procedural)")
    p.add_argument("--out")
    p.add_argument("--seconds", type=int, default=240)
    p.set_defaults(fn=cmd_make_bgm)
    p = sub.add_parser("import-story")
    p.add_argument("path")
    p.set_defaults(fn=cmd_import)
    sub.add_parser("youtube-auth", help="authorise YouTube uploads (OAuth, loopback)").set_defaults(fn=cmd_youtube_auth)
    sub.add_parser("tiktok-auth", help="authorise TikTok inbox uploads (OAuth)").set_defaults(fn=cmd_tiktok_auth)
    p = sub.add_parser("smoke")
    p.add_argument("--image-provider", choices=["mock", "codex", "local"])
    p.add_argument("--fresh", action="store_true", help="delete the sample story's episodes first")
    p.add_argument("--skip-regen", action="store_true")
    p.add_argument("--skip-final", action="store_true")
    p.set_defaults(fn=cmd_smoke)
    args = ap.parse_args(argv)
    from .drive import DriveError
    import httpx
    from .audio_engine.gate import AudioQCError
    try:
        args.fn(args)
    except DriveError as exc:
        ap.exit(1, f"Lỗi Google Drive: {exc}\n")
    except AudioQCError as exc:
        ap.exit(2, f"Audio QC blocked: {exc}\n")
    except (httpx.HTTPError, OSError) as exc:
        if args.cmd not in {"drive-auth", "drive-import"}:
            raise
        ap.exit(1, f"Lỗi kết nối/file Google Drive: {type(exc).__name__}. Kiểm tra mạng và đường dẫn JSON.\n")


if __name__ == "__main__":
    main()
