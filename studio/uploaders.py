"""Uploaders for approved publishing records. They only ever run after a human approved the `publish`
approval, and only when PUBLISH_UPLOAD_ENABLED=true:

- YouTube Data API v3 resumable upload, privacy from YOUTUBE_PRIVACY (default private) + Vietnamese captions.
- TikTok Content Posting API "inbox" upload: the video lands in the creator's TikTok drafts/inbox and the
  creator finishes posting in the app. Nothing is posted publicly by this code.

OAuth tokens live in storage/secrets/*.json (chmod 600, git-ignored); client secrets come from .env.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
from pathlib import Path

import httpx

from .config import Settings, get_settings

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
YT_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
YT_CAPTIONS = "https://www.googleapis.com/upload/youtube/v3/captions"
YT_SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.force-ssl"

TT_AUTH = "https://www.tiktok.com/v2/auth/authorize/"
TT_TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"
TT_INBOX_INIT = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
TT_STATUS = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

CHUNK = 8 * 1024 * 1024
TT_MAX_SINGLE = 64 * 1024 * 1024


class UploadError(RuntimeError):
    pass


# ------------------------------------------------------------------ token storage

def token_path(platform: str, s: Settings) -> Path:
    return s.storage / "secrets" / f"{platform}_token.json"


def load_token(platform: str, s: Settings) -> dict | None:
    p = token_path(platform, s)
    return json.loads(p.read_text()) if p.exists() else None


def save_token(platform: str, data: dict, s: Settings) -> None:
    p = token_path(platform, s)
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    tmp.replace(p)


def configured(platform: str, s: Settings) -> bool:
    return s.publish_upload_enabled and load_token(platform, s) is not None


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


# ------------------------------------------------------------------ YouTube

def google_client(s: Settings) -> dict:
    path = s.project_path(s.youtube_client_secrets)
    if not path or not path.exists():
        raise UploadError("YOUTUBE_CLIENT_SECRETS file not found (OAuth client of type Desktop app)")
    data = json.loads(path.read_text())
    return data.get("installed") or data.get("web") or data


def youtube_auth_url(s: Settings, redirect_uri: str, state: str, challenge: str) -> str:
    q = {"client_id": google_client(s)["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
         "scope": YT_SCOPES, "access_type": "offline", "prompt": "consent", "state": state,
         "code_challenge": challenge, "code_challenge_method": "S256"}
    return f"{GOOGLE_AUTH}?{urllib.parse.urlencode(q)}"


def youtube_exchange(s: Settings, code: str, redirect_uri: str, verifier: str, client: httpx.Client) -> dict:
    cl = google_client(s)
    r = client.post(GOOGLE_TOKEN, data={"code": code, "client_id": cl["client_id"], "client_secret": cl["client_secret"],
                                        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
                                        "code_verifier": verifier})
    if r.status_code != 200:
        raise UploadError(f"google token exchange failed: {r.status_code} {r.text[:300]}")
    tok = r.json()
    if not tok.get("refresh_token"):
        raise UploadError("google returned no refresh_token (revoke the app's access and retry)")
    data = {"access_token": tok["access_token"], "refresh_token": tok["refresh_token"],
            "expires_at": time.time() + int(tok.get("expires_in", 3600)) - 60}
    save_token("youtube", data, s)
    return data


def youtube_access_token(s: Settings, client: httpx.Client) -> str:
    tok = load_token("youtube", s)
    if tok is None:
        raise UploadError("YouTube not authorised: run `python -m studio.cli youtube-auth`")
    if tok["expires_at"] > time.time():
        return tok["access_token"]
    cl = google_client(s)
    r = client.post(GOOGLE_TOKEN, data={"client_id": cl["client_id"], "client_secret": cl["client_secret"],
                                        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
    if r.status_code != 200:
        raise UploadError(f"google token refresh failed: {r.status_code} {r.text[:300]}")
    body = r.json()
    tok.update(access_token=body["access_token"], expires_at=time.time() + int(body.get("expires_in", 3600)) - 60)
    save_token("youtube", tok, s)
    return tok["access_token"]


def youtube_upload(record: dict, s: Settings, client: httpx.Client, srt: Path | None = None) -> dict:
    video = Path(record["video_path"])
    size = video.stat().st_size
    auth = {"Authorization": f"Bearer {youtube_access_token(s, client)}"}
    meta = {"snippet": {"title": record["title"][:100], "description": record.get("description") or "",
                        "tags": list(record.get("tags") or []), "categoryId": s.youtube_category_id,
                        "defaultLanguage": "vi", "defaultAudioLanguage": "vi"},
            "status": {"privacyStatus": s.youtube_privacy, "selfDeclaredMadeForKids": False,
                       "containsSyntheticMedia": True}}
    r = client.post(YT_UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"}, json=meta,
                    headers={**auth, "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
    if r.status_code != 200 or "location" not in r.headers:
        raise UploadError(f"youtube init failed: {r.status_code} {r.text[:300]}")
    session = r.headers["location"]
    offset, result = 0, None
    with open(video, "rb") as f:
        while offset < size:
            f.seek(offset)
            chunk = f.read(CHUNK)
            end = offset + len(chunk) - 1
            r = client.put(session, content=chunk, headers={**auth, "Content-Range": f"bytes {offset}-{end}/{size}"},
                           timeout=600)
            if r.status_code in (200, 201):
                result = r.json()
                break
            if r.status_code == 308:  # resume incomplete: continue after the last byte the server holds
                rng = r.headers.get("range")
                offset = int(rng.split("-")[1]) + 1 if rng else 0
                continue
            raise UploadError(f"youtube chunk upload failed: {r.status_code} {r.text[:300]}")
    if not result or "id" not in result:
        raise UploadError("youtube upload finished without a video id")
    out = {"external_id": result["id"], "url": f"https://youtu.be/{result['id']}", "privacy": s.youtube_privacy}
    if srt and srt.exists():
        out["captions"] = _youtube_captions(result["id"], srt, auth, client)
    return out


def _youtube_captions(video_id: str, srt: Path, auth: dict, client: httpx.Client) -> str:
    boundary = secrets.token_hex(12)
    meta = json.dumps({"snippet": {"videoId": video_id, "language": "vi", "name": "Tiếng Việt"}})
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
            f"--{boundary}\r\nContent-Type: application/x-subrip\r\n\r\n").encode() + srt.read_bytes() + \
        f"\r\n--{boundary}--\r\n".encode()
    r = client.post(YT_CAPTIONS, params={"uploadType": "multipart", "part": "snippet"}, content=body,
                    headers={**auth, "Content-Type": f"multipart/related; boundary={boundary}"})
    return "ok" if r.status_code in (200, 201) else f"failed {r.status_code}"  # captions are best-effort


# ------------------------------------------------------------------ TikTok

def tiktok_auth_url(s: Settings, state: str, challenge: str) -> str:
    q = {"client_key": s.tiktok_client_key, "scope": "user.info.basic,video.upload", "response_type": "code",
         "redirect_uri": s.tiktok_redirect_uri, "state": state,
         "code_challenge": challenge, "code_challenge_method": "S256"}
    return f"{TT_AUTH}?{urllib.parse.urlencode(q)}"


def _tiktok_token_request(s: Settings, client: httpx.Client, **data) -> dict:
    r = client.post(TT_TOKEN, data={"client_key": s.tiktok_client_key, "client_secret": s.tiktok_client_secret,
                                     **data}, headers={"Content-Type": "application/x-www-form-urlencoded"})
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or "access_token" not in body:
        raise UploadError(f"tiktok token request failed: {r.status_code} {r.text[:300]}")
    tok = {"access_token": body["access_token"], "refresh_token": body["refresh_token"],
           "open_id": body.get("open_id"), "expires_at": time.time() + int(body.get("expires_in", 86400)) - 60}
    save_token("tiktok", tok, s)
    return tok


def tiktok_exchange(s: Settings, code: str, verifier: str, client: httpx.Client) -> dict:
    return _tiktok_token_request(s, client, code=code, grant_type="authorization_code",
                                 redirect_uri=s.tiktok_redirect_uri, code_verifier=verifier)


def tiktok_access_token(s: Settings, client: httpx.Client) -> str:
    tok = load_token("tiktok", s)
    if tok is None:
        raise UploadError("TikTok not authorised: run `python -m studio.cli tiktok-auth`")
    if tok["expires_at"] > time.time():
        return tok["access_token"]
    return _tiktok_token_request(s, client, grant_type="refresh_token", refresh_token=tok["refresh_token"])["access_token"]


def tiktok_upload(record: dict, s: Settings, client: httpx.Client) -> dict:
    video = Path(record["video_path"])
    size = video.stat().st_size
    if size > TT_MAX_SINGLE:
        chunk, count = CHUNK * 2, size // (CHUNK * 2)  # last chunk absorbs the remainder (TikTok rule)
    else:
        chunk, count = size, 1
    auth = {"Authorization": f"Bearer {tiktok_access_token(s, client)}"}
    r = client.post(TT_INBOX_INIT, headers={**auth, "Content-Type": "application/json; charset=UTF-8"},
                    json={"source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk,
                                          "total_chunk_count": count}})
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or (body.get("error") or {}).get("code") not in (None, "ok"):
        raise UploadError(f"tiktok init failed: {r.status_code} {r.text[:300]}")
    upload_url, publish_id = body["data"]["upload_url"], body["data"]["publish_id"]
    with open(video, "rb") as f:
        for i in range(count):
            start = i * chunk
            end = size - 1 if i == count - 1 else start + chunk - 1
            f.seek(start)
            data = f.read(end - start + 1)
            r = client.put(upload_url, content=data, timeout=600,
                           headers={"Content-Type": "video/mp4", "Content-Range": f"bytes {start}-{end}/{size}"})
            if r.status_code not in (200, 201, 206):
                raise UploadError(f"tiktok chunk {i + 1}/{count} failed: {r.status_code} {r.text[:300]}")
    status = None
    r = client.post(TT_STATUS, headers={**auth, "Content-Type": "application/json; charset=UTF-8"},
                    json={"publish_id": publish_id})
    if r.status_code == 200:
        status = ((r.json().get("data") or {}).get("status"))
    return {"external_id": publish_id, "status": status or "UPLOADED", "mode": "inbox_draft"}


def upload_record(record: dict, s: Settings | None = None, client: httpx.Client | None = None,
                  srt: Path | None = None) -> dict:
    s = s or get_settings()
    own = client is None
    client = client or httpx.Client(timeout=120)
    try:
        if record["platform"] == "youtube":
            return youtube_upload(record, s, client, srt)
        if record["platform"] == "tiktok":
            return tiktok_upload(record, s, client)
        raise UploadError(f"unsupported platform {record['platform']}")
    finally:
        if own:
            client.close()
