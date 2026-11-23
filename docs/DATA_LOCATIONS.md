# Dữ liệu ngoài git — vị trí lưu trữ

Cập nhật: 2026-11-23 · Máy: Mac mini M4 (máy sản xuất hiện tại)

Git chỉ chứa code và tài liệu. Toàn bộ tài liệu gốc, video, audio, model và thông tin đăng nhập nằm ở các đường dẫn dưới đây (tương đối với thư mục gốc dự án) và **chưa có bản sao nào khác**. File này dùng làm đầu vào cho kế hoạch backup sang máy khác sau này.

Tổng dung lượng: khoảng **13 GB** (`storage/`) + 300 KB (`document/`) + database PostgreSQL.

## 1. Không tạo lại được — ưu tiên backup cao nhất

| Đường dẫn | Dung lượng | Nội dung |
|---|---|---|
| `document/` | 300 KB | Bản thảo truyện `.docx` (6 file): 3 bộ trọng sinh 12 tập (`01_…`, `02_…`, `03_…`) và bộ "Ngôi Làng Không Có Người Chết" (story bible tập 1, tập 2, trọn bộ 12 tập) |
| `storage/stories/` | 40 KB | Bản gốc truyện đã nhập vào hệ thống |
| `storage/production/` | 400 KB | Bộ sản xuất V4.1 "Ngôi Làng…" (manifest, config, narration từng tập) |
| `storage/secrets/` | 12 KB | OAuth client secret + token Google Drive, YouTube. **Nhạy cảm** — chỉ backup vào nơi được mã hoá |
| `.env` | — | Cấu hình + bot token, API key. **Nhạy cảm** — như trên |
| PostgreSQL `audio_story_studio` | — | Stories, episodes, jobs, approvals, chi phí, publishing records. Backup bằng `pg_dump audio_story_studio > backup.sql` (không nằm trong `storage/`) |

## 2. Thành phẩm — tạo lại được nhưng tốn nhiều thời gian

| Đường dẫn | Dung lượng | Nội dung |
|---|---|---|
| `storage/output/<episode_id>/` | 2.6 GB | Video final 1080p, TikTok 9:16. Tập 8 chiếm 2.3 GB (nhiều bản dựng lại) |
| `storage/previews/<episode_id>/preview_720p.mp4` | 69 MB | Preview 720p tập 1–7 |
| `storage/audio/episodes/<episode_id>/` | 7.1 GB | Narration, repair, mix, master từng tập |
| `storage/episodes/<episode_id>/` | 889 MB | Script, timeline, scene plan, phụ đề, QC report, clip cache |
| `storage/assets/` | 88 MB | Ảnh cảnh/bối cảnh đã sinh (`scenes/`, `environments/`), âm thanh procedural (`sound/`), BGM (`bgm/`) |

Toàn bộ `storage/` hiện có 187 file video (`.mp4`), tổng khoảng 2.3 GB.

## 3. Tải lại hoặc sinh lại được — không cần backup

| Đường dẫn | Dung lượng | Cách khôi phục |
|---|---|---|
| `storage/tools/` | 1.4 GB | uv + venv VieNeu (Python 3.12) — cài lại theo `docs/AUDIO_ENGINE.md` |
| `storage/models/` | 1.1 GB | VieNeu (580 MB), whisper-small (464 MB), Piper (60 MB) — tải lại |
| `storage/audio/cache/`, `storage/audio/previews/` | 160 MB | Cache TTS, tự sinh lại khi chạy |
| `storage/logs/`, `storage/research/`, `storage/imports/` | ~7 MB | Log và dữ liệu khảo sát |

## Ghi chú cho kế hoạch backup

- Nhóm 1 nhỏ (< 1 MB, chưa tính DB) nhưng quan trọng nhất: nên backup thường xuyên và có mã hoá.
- Nhóm 2 lớn (~10.7 GB): cân nhắc chỉ giữ bản final/master mới nhất của mỗi tập, bỏ các bản dựng thử, trước khi chép sang máy khác.
- **Đường dẫn đang lưu dạng tuyệt đối**: 167 file JSON trong `storage/episodes/` và `storage/production/` chứa `/Users/vangnguyen/...`; cột `visual_assets.path` trong DB trỏ tới vị trí cũ `/Users/vangnguyen/Projects/audio-story-studio/` (trước khi dự án chuyển sang `~/dev/projects/`). Khi khôi phục ở máy khác cần đặt dự án đúng đường dẫn cũ, hoặc viết script đổi tiền tố đường dẫn trong JSON + DB (nên làm trước khi backup).
