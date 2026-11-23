"""Read-only Google Drive intake. OAuth credentials are separate from YouTube."""
from __future__ import annotations

import hashlib
import http.server
import io
import json
import re
import secrets
import time
import urllib.parse
import webbrowser
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import httpx
import yaml

from .config import Settings
from .uploaders import GOOGLE_AUTH, GOOGLE_TOKEN, load_token, pkce_pair, save_token

SCOPE = "https://www.googleapis.com/auth/drive.readonly"
API = "https://www.googleapis.com/drive/v3/files"
DOC = "application/vnd.google-apps.document"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_BYTES = 20 * 1024 * 1024


class DriveError(ValueError):
    pass


def client_config(s: Settings) -> dict:
    path = s.project_path(s.google_drive_client_secrets)
    if not path or not path.exists():
        candidates = list((s.storage / "secrets").glob("client_secret_*.json"))
        if len(candidates) != 1:
            raise DriveError("Cần đặt GOOGLE_DRIVE_CLIENT_SECRETS trỏ tới file JSON Desktop app.")
        path = candidates[0]
    try:
        data = json.loads(path.read_text())["installed"]
        if not data.get("client_id") or not data.get("client_secret"):
            raise KeyError("credentials")
    except (ValueError, KeyError) as e:
        raise DriveError("File OAuth phải có cấu hình Desktop app (installed).") from e
    return data


def checked(r: httpx.Response) -> httpx.Response:
    if r.status_code >= 400:
        hints = {401: "Token không còn hợp lệ; chạy lại drive-auth.",
                 403: "Kiểm tra Drive API đã bật, quyền đọc tài liệu và tài khoản đăng nhập.",
                 404: "Không tìm thấy tài liệu hoặc tài khoản chưa được chia sẻ quyền đọc."}
        raise DriveError(f"Google HTTP {r.status_code}. " + hints.get(r.status_code, "Hãy thử lại sau."))
    return r


def store_token(body: dict, s: Settings, previous: dict | None = None) -> dict:
    data = {**(previous or {}), **body}
    if not data.get("refresh_token") or not data.get("access_token"):
        raise DriveError("Không nhận đủ token; chạy lại drive-auth và cấp quyền.")
    data["expires_at"] = time.time() + int(body.get("expires_in", 3600)) - 60
    save_token("google_drive", data, s)
    return data


def access_token(s: Settings, client: httpx.Client) -> str:
    tok = load_token("google_drive", s)
    if not tok:
        raise DriveError("Chưa đăng nhập Drive. Chạy: python -m studio.cli drive-auth")
    if tok.get("expires_at", 0) <= time.time():
        cfg = client_config(s)
        body = checked(client.post(GOOGLE_TOKEN, data={
            "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
            "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})).json()
        tok = store_token(body, s, tok)
    return tok["access_token"]


def authorize(s: Settings, *, no_browser: bool = False, timeout: int = 300) -> None:
    cfg = client_config(s)
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(32)
    got: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            valid = secrets.compare_digest(q.get("state", ""), state)
            if valid:
                got.update(q)
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(("Đã nhận phản hồi Google. Quay lại Terminal." if valid else "Invalid state").encode())

        def log_message(self, *args):
            pass

    with http.server.HTTPServer(("127.0.0.1", 0), Handler) as srv:
        redirect = f"http://127.0.0.1:{srv.server_port}/"
        query = {"client_id": cfg["client_id"], "redirect_uri": redirect,
                 "response_type": "code", "scope": SCOPE, "access_type": "offline",
                 "prompt": "consent", "state": state, "code_challenge": challenge,
                 "code_challenge_method": "S256"}
        url = GOOGLE_AUTH + "?" + urllib.parse.urlencode(query)
        print("Mở link trên trình duyệt CÙNG MÁY, đăng nhập tài khoản có truyện:\n" + url, flush=True)
        if not no_browser:
            webbrowser.open(url)
        srv.timeout = 1
        deadline = time.monotonic() + timeout
        while not (got.get("code") or got.get("error")) and time.monotonic() < deadline:
            srv.handle_request()
    if not got.get("code"):
        raise DriveError("Chưa được cấp quyền (từ chối hoặc hết thời gian). Chạy lại drive-auth.")
    with httpx.Client(timeout=30) as client:
        body = checked(client.post(GOOGLE_TOKEN, data={
            "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
            "grant_type": "authorization_code", "code": got["code"],
            "redirect_uri": redirect, "code_verifier": verifier})).json()
    store_token(body, s)
    print("Đã kết nối Google Drive. Token được lưu riêng trong storage/secrets (quyền 600).")


def file_id(value: str) -> str:
    value = value.strip()
    if "://" in value:
        u = urllib.parse.urlparse(value)
        if u.scheme != "https" or u.hostname not in {"docs.google.com", "drive.google.com"}:
            raise DriveError("Chỉ nhận link HTTPS của docs.google.com hoặc drive.google.com.")
        match = re.search(r"/d/([\w-]+)", u.path)
        value = match.group(1) if match else urllib.parse.parse_qs(u.query).get("id", [""])[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", value):
        raise DriveError("Link hoặc ID tài liệu không hợp lệ.")
    return value


def download(client: httpx.Client, url: str, headers: dict, params: dict) -> bytes:
    with client.stream("GET", url, headers=headers, params=params) as r:
        checked(r)
        parts, size = [], 0
        for part in r.iter_bytes():
            size += len(part)
            if size > MAX_BYTES:
                raise DriveError("Tài liệu vượt giới hạn 20 MB.")
            parts.append(part)
    return b"".join(parts)


def docx_text(raw: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_BYTES:
                raise DriveError("Nội dung DOCX vượt giới hạn 20 MB.")
            root = ET.fromstring(z.read(info))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        lines = []
        for p in root.findall(".//w:body//w:p", ns):
            text = "".join(n.text or "" for n in p.findall(".//w:t", ns))
            style = p.find("w:pPr/w:pStyle", ns)
            level = style.get("{" + ns["w"] + "}val", "") if style is not None else ""
            if level.lower() in {"heading1", "heading2"}:
                text = "## " + text
            lines.append(text)
        return "\n\n".join(lines)
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise DriveError("Không đọc được file DOCX. Hãy chuyển sang Google Docs hoặc TXT.") from e


def fetch_story(source: str, s: Settings, client: httpx.Client) -> Path:
    fid = file_id(source)
    headers = {"Authorization": "Bearer " + access_token(s, client)}
    url = f"{API}/{fid}"
    meta = checked(client.get(url, headers=headers, params={
        "fields": "id,name,mimeType,capabilities(canDownload)", "supportsAllDrives": "true"})).json()
    if not meta.get("capabilities", {}).get("canDownload", False):
        raise DriveError("Tài khoản không được phép tải tài liệu này.")
    mime, name = meta["mimeType"], meta["name"]
    if mime == DOC:
        raw = download(client, url + "/export", headers, {"mimeType": "text/plain"})
        text = raw.decode("utf-8-sig")
    elif mime == DOCX or name.lower().endswith(".docx"):
        text = docx_text(download(client, url, headers, {"alt": "media", "supportsAllDrives": "true"}))
    elif mime in {"text/plain", "text/markdown", "text/x-markdown"} or Path(name).suffix.lower() in {".txt", ".md"}:
        try:
            text = download(client, url, headers, {"alt": "media", "supportsAllDrives": "true"}).decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise DriveError("File TXT/Markdown cần mã hóa UTF-8.") from e
    else:
        raise DriveError("Chỉ hỗ trợ Google Docs, DOCX, TXT và Markdown; hãy chuyển đổi tài liệu trước.")
    if not text.strip():
        raise DriveError("Tài liệu không có nội dung văn bản.")
    # Keep original front matter; otherwise add title and stable source-specific slug.
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        meta_yaml = yaml.safe_dump({"title": Path(name).stem, "slug": "drive-" + fid,
                                   "rights": "unknown", "source_url": f"https://drive.google.com/file/d/{fid}/view"},
                                  allow_unicode=True, sort_keys=False)
        text = "---\n" + meta_yaml + "---\n\n" + text
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    folder = s.storage / "imports" / "google_drive"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{fid}-{digest}.md"
    path.write_text(text, encoding="utf-8")
    return path
