# TASKS — Audio Story Studio

Cập nhật: 2026-11-22

## Kết quả kiểm thử (đã chạy thật trên Mac mini M4)

| Kiểm thử | Lệnh | Kết quả |
|---|---|---|
| Unit + DB + media + API + integration + audio engine | `.venv/bin/python -m pytest -q` | **138 passed** (2026-11-23) |
| Smoke với Piper + burn-in + BGM + TikTok 9:16 | `TTS_PROVIDER=piper … smoke --fresh` | **SMOKE PASSED** (TikTok 82,6 s = cảnh chiến đấu, 1080×1920, −14,4 LUFS, QC đạt) |
| Smoke với Piper TTS + phụ đề burn-in + BGM | `TTS_PROVIDER=piper … smoke --fresh` | **SMOKE PASSED** (pipeline 28 s, narration 5,3 phút, −16,1 LUFS, regen 1/4 clip) |
| Smoke end-to-end (truyện mẫu, TTS `say` Linh, ảnh mock) | `.venv/bin/python -m studio.cli smoke --fresh` | **SMOKE PASSED** |
| Codex ImageGen headless qua `CodexImageProvider` | `.venv/bin/python -m studio.cli check-codex-imagegen` | OK, 1 ảnh 1920×1080, 38 s |
| Import n8n (n8n 2.48.7, Node 24) | `n8n import:workflow --separate` | **8/8 workflow import thành công** |
| Chạy thật n8n → Worker API → job PostgreSQL | WF-06 / WF-01 qua webhook | 403 không auth · 400 input sai · 202 job mới · 202 cùng job khi gọi lặp (idempotent) · 400 path traversal |

Chi tiết smoke (episode #2):
- Narration 7,2 phút (431,08 s), 121 câu phụ đề, −16,2 LUFS, không khoảng lặng > 3 s.
- 4 cảnh 95–113 s, ranh giới trùng 4 chương; mood: mysterious → dread → battle → hope; location đúng từng chương.
- Ảnh: tái sử dụng thư viện (match `exact`), 0 ảnh mới; lần chạy đầu tạo đúng 4 ảnh (giới hạn 4).
- Preview 1280×720 và final 1920×1080: h264 + aac + mov_text, 10 777 frame, không black frame, QC passed.
- `/regen_scene 2 3`: chỉ render lại 1/4 clip (3 clip dùng cache), 14,6 s.
- Publishing record `awaiting_approval` (không tự xuất bản). Telegram outbox: 1 sendVideo + 8 sendMessage.

## Phase 0 — Audit & Planning ✅
- [x] Audit môi trường, công cụ, phiên bản (PLAN.md §0)
- [x] Xác minh Codex ImageGen headless (hoạt động, không dùng API key)
- [x] PLAN.md, ARCHITECTURE.md, TASKS.md

## Phase 1 — MVP End-to-End ✅
- [x] Schema + migrations + PostgreSQL job queue (idempotency, retry, timeout, lock recovery)
- [x] Story intake + rights approval gate
- [x] Script Builder
- [x] TTS local (macOS Linh) + cache + loudnorm + transcript timing
- [x] Scene planner theo nội dung/timestamp (45/120/150 s), phủ kín không gap/overlap
- [x] Prompt Builder + style profile `dark_fantasy_cinematic`
- [x] Asset matcher (chống sai nhân vật/bối cảnh) + Mock/Codex/Local providers
- [x] Timeline + phụ đề SRT
- [x] FFmpeg: 4 motion preset, clip cache, xfade không black frame, khớp duration
- [x] QC tự động; preview 720p → duyệt → final 1080p
- [x] Telegram: 10 lệnh + inline keyboard duyệt từng cảnh/tập, allowlist, dry-run
- [x] Worker API (FastAPI, API key) + 8 workflow n8n (đã import & chạy thử)
- [x] Budget Manager (reservation, chặn API trả phí)
- [x] Unit/integration/smoke tests

## Việc cần người dùng làm để chạy production
- [ ] Tạo bot với @BotFather → `TELEGRAM_BOT_TOKEN`; lấy user ID → `TELEGRAM_ALLOWED_USER_IDS`
- [ ] Chọn chế độ Telegram: `polling` (đơn giản) hoặc `n8n_webhook` (cần HTTPS tunnel chỉ mở `/webhook/*`)
- [ ] Khởi động Docker Desktop (daemon không chạy trong phiên kiểm thử) → `docker compose up -d` → `scripts/n8n_import.sh`
- [ ] Trong n8n: tạo credential `ASS Webhook Auth` (Header Auth) và `Audio Story Bot` (Telegram), publish workflow
- [ ] Muốn dùng ảnh thật: `IMAGE_PROVIDER=codex`, `CODEX_IMAGEGEN_VERIFIED=true` (đã kiểm chứng)

## Phase 2 — Visual Automation (còn lại)
- [ ] Chạy trọn 1 tập với `IMAGE_PROVIDER=codex` và đánh giá consistency nhân vật
- [ ] Character reference sheets (ảnh tham chiếu nhân vật) đưa vào prompt/edit
- [ ] LocalImageProvider với backend thật (Draw Things/ComfyUI) nếu muốn bỏ phụ thuộc quota
- [ ] Phân tích cảnh bằng LLM (Claude/Codex CLI) thay heuristic từ khoá — tuỳ chọn
- [ ] Mood khi reuse ảnh: hiện ưu tiên đúng nhân vật/bối cảnh, mood chỉ là điểm cộng

## Phase 3 — Production & TikTok
- [x] TikTok 9:16 60–120 s: chọn đoạn cao trào theo `intensity` (đoạn văn trọn vẹn), header tiêu đề, phụ đề lớn đặt ở 62% chiều cao (tránh UI TikTok), −14 LUFS, QC riêng
- [ ] TikTok: crop thông minh theo chủ thể (hiện crop giữa + pan), CTA cuối video
- [x] Phụ đề burn-in bằng overlay PNG (Pillow, font Arial Bold có dấu tiếng Việt) — không cần libass
- [x] BGM dark ambient tự sinh (`studio.cli make-bgm`, không vướng bản quyền), trộn −10 dB dưới giọng đọc
- [x] TTS neural local: Piper `vi_VN-vais1000-medium` (`TTS_PROVIDER=piper`)
- [ ] Thư viện BGM đa dạng hơn (nhạc có giấy phép) nếu muốn
- [x] Uploader YouTube (resumable, private mặc định, caption vi) + TikTok (inbox/nháp) — chỉ sau approval `publish`, test bằng HTTP giả lập
- [ ] Chạy upload thật: cần bạn tạo OAuth client YouTube / app TikTok (xem README)
- [ ] TTS tiếng Việt chất lượng cao hơn (VieNeu-TTS / F5-TTS-vi) — cần PyTorch
- [ ] Dashboard tiến độ/chi phí

## Review toàn dự án (2026-11-22) — đã sửa 10 lỗi, có test hồi quy (`tests/test_review_fixes.py`)
- [x] /publish lại sau khi từ chối: bản ghi gắn lại vào approval mới
- [x] Không upload lại khi upload bị gián đoạn (tránh video YouTube trùng)
- [x] /publish và publish_queue chỉ nhận bản final đã qua QC
- [x] /preview tập đã duyệt chỉ gửi lại video, không mở lại vòng duyệt
- [x] /cancel dừng cả bước đang chạy (không nối bước tiếp)
- [x] Worker heartbeat; job làm chết worker → thất bại sau max_attempts thay vì lặp vô hạn
- [x] TikTok lỗi không chặn yêu cầu đăng YouTube
- [x] Ảnh bị từ chối không bao giờ gán lại cho cảnh đó (`scenes.rejected_asset_ids`)
- [x] Bot polling sống sót qua lỗi mạng; worker không sập khi Telegram lỗi
- [x] Dán truyện bằng nhiều tin nhắn + `/xong` (dùng trên điện thoại); `scripts/services.sh` chạy nền launchd

## Giới hạn đã biết
- FFmpeg Homebrew không có libass/drawtext; đã thay bằng overlay Pillow (`SUBTITLE_MODE=both`: burn-in + track mềm + `.srt`).
- Regen trên provider mock: prompt mang `request_id` để ảnh mới luôn khác ảnh đã bị từ chối (đã sửa lỗi tái hiện asset retired).
- Timing phụ đề ở cấp câu (theo thời lượng chunk thật), chưa forced alignment cấp từ.
- Bot API giới hạn upload 50 MB: preview lớn hơn sẽ gửi teaser 60 s + đường dẫn file.
- n8n chính thức hỗ trợ Node 22/24; máy dùng Node 25 nên chạy n8n bằng Docker (khuyến nghị) hoặc Node 24.
