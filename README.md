# Audio Story Studio

Sản xuất video truyện audio tiếng Việt phong cách Dark Fantasy Cinematic trên Mac mini M4:
Telegram → n8n → Script → TTS → Visual Story Engine → FFmpeg → QC → duyệt → Publishing queue.

**Dùng từ điện thoại: [HUONG_DAN_DIEN_THOAI.md](HUONG_DAN_DIEN_THOAI.md)**

Tài liệu: [PLAN.md](PLAN.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [TASKS.md](TASKS.md)

Audio engine nâng cấp: [cài đặt, DSP/QC, mix/master, CLI và n8n](docs/AUDIO_ENGINE.md).
Chạy bản thử riêng, không đăng hay sửa video hiện có: `.venv/bin/python -m studio.cli episode render 8 --short`.
QC audio bắt buộc kiểm file AAC thực tế và nguồn phụ thuộc; lỗi nghiêm trọng hoặc nghe duyệt chưa hoàn tất sẽ chặn xuất bản.

## Cài đặt

```zsh
cd ~/dev/projects/audio-story-studio
python3.14 -m venv .venv && .venv/bin/pip install -e ".[dev]"
createdb audio_story_studio && createdb audio_story_studio_test
cp .env.example .env && chmod 600 .env     # điền API_KEY, Telegram…
.venv/bin/python -m studio.cli migrate
(mkdir -p storage/models/piper && cd storage/models/piper && ../../../.venv/bin/python -m piper.download_voices vi_VN-vais1000-medium)
.venv/bin/python -m studio.cli make-bgm         # nhạc nền dark ambient tự sinh
.venv/bin/python -m studio.cli check-env
```

Yêu cầu: PostgreSQL, FFmpeg, macOS `say` với giọng **Linh** (vi_VN). Tuỳ chọn: Codex CLI (đăng nhập ChatGPT), Docker.

## Chạy

Chạy nền 24/7 (launchd): `scripts/services.sh install` · `status` · `logs telegram` · `uninstall`.
Hoặc chạy tay:

```zsh
.venv/bin/python -m studio.worker                 # worker xử lý job
.venv/bin/uvicorn studio.api:app --host 127.0.0.1 --port 8765   # API cho n8n
.venv/bin/python -m studio.telegram_poll          # nếu TELEGRAM_MODE=polling
```

n8n (Docker): `docker compose --env-file .env up -d` rồi `scripts/n8n_import.sh`.
Trong n8n tạo credential **ASS Webhook Auth** (Header Auth) và **Audio Story Bot** (Telegram API).

## Telegram

`/start` · `/newstory` (gửi file .md/.txt, hoặc `/newstory sample`) · `/approve rights <story_id>` ·
`/create_episode <story_id>` · `/status [ep]` · `/preview <ep>` · `/regen_scene <ep> <scene>` ·
`/approve <ep>` · `/publish <ep>` · `/budget` · `/cancel <ep>`

## Kiểm thử

```zsh
.venv/bin/python -m pytest -q                       # 64 test (dùng DB *_test)
.venv/bin/python -m studio.cli smoke --fresh        # end-to-end với truyện mẫu
.venv/bin/python -m studio.cli check-codex-imagegen # tạo 1 ảnh qua Codex built-in image_gen
```

## Định dạng truyện

Markdown, front matter YAML tuỳ chọn (`title`, `rights: original|licensed|public_domain`, `characters`,
`locations` với `aliases` và `visual` tiếng Anh cho prompt). Chương bắt đầu bằng `## `, ngắt cảnh bằng `***`.
Xem `samples/ngon-den-cuoi-cung-cua-vuc-tro.md`.

## Đầu ra

- `storage/output/<ep>/youtube_1080p.mp4` — 16:9, phụ đề burn-in + track mềm.
- `storage/output/<ep>/tiktok_1080x1920.mp4` — 9:16, 60–120 s đoạn cao trào (`TIKTOK_*` trong `.env`).
- Cả hai vào `publishing_records` ở trạng thái chờ duyệt; không bao giờ tự đăng khi chưa duyệt.

## Tự đăng sau khi duyệt (tuỳ chọn, mặc định tắt)

1. `PUBLISH_UPLOAD_ENABLED=true` trong `.env`.
2. YouTube: Google Cloud → bật *YouTube Data API v3* → OAuth client loại **Desktop app** → tải JSON về
   `storage/secrets/youtube_client_secret.json` → `.venv/bin/python -m studio.cli youtube-auth`.
   Video được upload ở `YOUTUBE_PRIVACY` (mặc định **private**) kèm phụ đề tiếng Việt.
3. TikTok: app trên developers.tiktok.com (Login Kit + Content Posting API, scope `video.upload`) → điền
   `TIKTOK_CLIENT_KEY/SECRET/REDIRECT_URI` → `.venv/bin/python -m studio.cli tiktok-auth`.
   Video vào **hộp nháp TikTok**; bạn tự bấm đăng trong app.
4. Bấm "📤 Duyệt xuất bản" trên Telegram → job `publish_upload` cho từng nền tảng đã cấu hình; nền tảng chưa
   cấu hình chuyển `ready_manual`. Token lưu `storage/secrets/*.json` (chmod 600, không commit).

## An toàn & chi phí

`ALLOW_PAID_API=false`, `ALLOW_AUTO_PUBLISH=false` mặc định. Không có provider trả phí nào được cấu hình;
Codex ImageGen dùng quota gói ChatGPT (giới hạn `CODEX_MAX_IMAGES_PER_DAY`, tối đa 4 ảnh mới/tập).

## Lấy truyện từ Google Drive

Bật Google Drive API, tạo OAuth client **Desktop app**, thêm email vào Test users nếu app ở Testing.
Lưu JSON tại `storage/secrets/google_drive_client_secret.json`, hoặc giữ tên `client_secret_*.json`
(nếu chỉ có một file). Có thể đặt đường dẫn khác bằng `GOOGLE_DRIVE_CLIENT_SECRETS` trong `.env`.

```zsh
.venv/bin/python -m studio.cli drive-auth
.venv/bin/python -m studio.cli drive-import 'LINK_GOOGLE_DOCS_HOAC_DRIVE' --download-only
.venv/bin/python -m studio.cli drive-import 'LINK_GOOGLE_DOCS_HOAC_DRIVE'
```

Đăng nhập trên trình duyệt cùng máy chạy lệnh, bằng tài khoản có quyền đọc truyện.
Scope `drive.readonly` đọc được các file tài khoản có quyền truy cập; không cần chia sẻ công khai.
Token riêng lưu tại `storage/secrets/google_drive_token.json`, quyền 600, không commit.
Nếu token bị thu hồi/hết hạn refresh, chạy lại `drive-auth` (app External Testing có thể cần cấp lại quyền).

Hỗ trợ Google Docs, DOCX, TXT/Markdown UTF-8 (tối đa 20 MB; export Google Docs còn chịu giới hạn Google).
Google Docs xuất plain text nên heading định dạng có thể mất: dùng dòng `## Tên chương` trong nội dung
để chia chương rõ ràng; DOCX Heading 1/2 được đổi thành chương. Chưa hỗ trợ PDF/OCR.
`--download-only` tải vào `storage/imports/google_drive` và báo số chương/ký tự, không cần DB.
Không có cờ này, truyện được import vào DB và trả `story_id`; duyệt quyền sử dụng và tạo tập qua Telegram
bằng `/approve rights <story_id>` rồi `/create_episode <story_id>`.
Không tự tạo audio hay tự duyệt. Nếu cùng truyện đã được import, lệnh trả `created=false`;
nội dung trong DB không được cập nhật tự động khi bạn sửa tài liệu trên Drive.

Bản nâng cấp Horror Audio Engine: xem [báo cáo triển khai và số đo tập thử](docs/AUDIO_UPGRADE_REPORT.md) và [hướng dẫn vận hành, QC, nghe duyệt](docs/AUDIO_ENGINE.md).
