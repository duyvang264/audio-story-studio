# ARCHITECTURE — Audio Story Studio

```
Telegram ──HTTPS webhook──▶ n8n WF-00 (allowlist) ──X-API-Key──▶ Worker API (FastAPI, 127.0.0.1:8765)
   ▲          (hoặc TELEGRAM_MODE=polling: studio.telegram_poll)            │
   │                                                                       ▼
   │                                                       PostgreSQL (jobs, approvals, costs…)
   │                                                                       │ claim (SKIP LOCKED)
   └──── sendMessage / sendVideo ◀── Worker (studio.worker) ◀──────────────┘
                                       │ TTS (say/piper/vieneu) · ffmpeg · codex exec
                                       ▼
                                   storage/ (local filesystem)
```

## Module (thư mục `studio/`)

| # | Module | File | Vai trò |
|---|---|---|---|
| 1 | Telegram Controller | `commands.py`, `telegram.py`, `telegram_poll.py` | Lệnh, inline keyboard, allowlist, dedupe `update_id`, dry-run outbox. |
| 2 | Story Manager | `story.py` (`import_story`, `create_episodes`), `drive.py` | Front matter YAML, chương `##`, bản quyền (`rights_status` + approval). Nhập truyện read-only từ Google Drive (OAuth riêng với YouTube). |
| 3 | Script Builder | `story.py` (`build_episode_script`) | Chuẩn hoá lời kể, section/paragraph/sentence, tag nhân vật/bối cảnh. |
| 3a | Story Validation | `horror_story.py` | Beat kinh dị cấu hình được, kiểm tra continuity/độ dài → `story_quality_report.json`; FAIL thì dừng pipeline. |
| 4 | TTS Engine | `audio.py`, `tts/base.py`, `tts/text.py`, `tts/vieneu_worker.py` | Provider `macos_say` / `piper` / `vieneu` (venv riêng, offline) / `tone`. Chunk theo câu, cache `sha256(provider|model|voice|rate|text)`, ghép sample-exact, `transcript_timeline.json`. `tts/text.py` chỉ sửa cách đọc, giữ nguyên text cho phụ đề. |
| 4a | Audio Engine | `audio_engine/` | QC nguồn (`analyzer`), sửa lỗi có giới hạn (`repair`), thư viện âm thanh có checksum (`assets`), cue theo lời kể (`soundscape`), mix 5 stem có ducking (`mixing`), master EBU R128 hai lượt (`mastering`), gate gắn kết quả đo/nghe với hash file (`gate`). Điều phối ở `production.py`. Xem `docs/AUDIO_ENGINE.md`. |
| 5 | Visual Story Engine | `visual/scene_planner.py`, `visual/prompts.py`, `visual/engine.py` | Scene plan (DP), prompt builder, hàng đợi tạo ảnh. |
| 6 | Asset Manager | `visual/matcher.py`, `visual/engine.py` (`register_asset`) | Thư viện ảnh, tag, reuse, chống sai nhân vật/bối cảnh, retire ảnh bị từ chối. |
| 7 | Timeline Composer | `timeline.py`, `bgm.py` | Frame-exact timing, phụ đề (≤2 dòng × 42 ký tự), BGM (tự sinh, không bản quyền), transition. |
| 8 | FFmpeg Engine | `render.py`, `media.py`, `subtitles.py` | Clip từng cảnh (zoompan, oversample 2×), cache, xfade, mux audio + mov_text. Phụ đề burn-in vẽ bằng Pillow (không cần libass). |
| 9 | Quality Control | `qc.py` | Stream, độ phân giải, duration ±0.1 s, số frame, black frame, silence > 3 s, loudness −20…−12 LUFS, coverage, phụ đề. |
| 10 | Publishing Manager | `pipeline.py` (`h_publish_queue`) | `publishing_records` (youtube + tiktok, chung 1 approval `publish`); không upload tự động. |
| 10a | Uploaders | `uploaders.py` (`h_publish_upload`) | Chỉ sau approval `publish` + `PUBLISH_UPLOAD_ENABLED`: YouTube resumable upload (private mặc định) + caption; TikTok inbox (nháp). OAuth PKCE, token chmod 600. |
| 10b | TikTok / Shorts | `shorts.py`, `qc.check_short` | Chọn đoạn cao trào (đoạn văn trọn vẹn, 60–120 s, intensity cao nhất), clip 9:16 từ cùng ảnh cảnh, header tiêu đề, phụ đề burn-in lớn, loudnorm −14 LUFS. |
| 11 | Budget Manager | `budget.py` | Bảng giá provider, reservation (advisory lock), commit/release, hạn mức tháng/hạng mục, chặn API trả phí. |

## Pipeline & job types

```
script_build → story_validate → scene_sound_analysis → tts → tts_qc → audio_repair → narration_qc
  → sound_assets → sound_timeline → audio_mix → audio_master → master_audio_qc
  → visual_plan → visual_generate → timeline → render_preview → qc_preview → notify_preview
                                                                                           │ (approval: content)
                         render_final → qc_final → render_tiktok → qc_tiktok → publish_queue
                                                                                           │ (approval: publish)
                                                       publish_upload (mỗi nền tảng đã cấu hình; còn lại ready_manual)
/regen_scene: visual_generate(regen) → timeline → render_preview(cache) → qc_preview → notify_preview
```

Job ngoài chuỗi: `drive_import`, `story_generate` (bản nháp từ model local, vẫn cần duyệt rights/content), `publish_upload`. `/regen_scene` đi qua `visual_generate` (regen). Mọi job type phải có handler (có test kiểm tra). Danh sách stage gốc nằm ở `studio/jobs.py` (`STAGES`, `POST_APPROVAL_STAGES`), handler ở `studio/pipeline.py` (`HANDLERS`).

- Idempotency key: `<stage>:<episode_id>:<run>`. Gọi lại cùng key trả về job cũ.
- Retry: `max_attempts=3`, backoff 15 s · 30 s · 60 s. QC fail không retry (lỗi nội dung).
- Lock hết hạn (`timeout_sec` theo stage) → tự động requeue.
- `job_events` lưu log theo episode; `/status` hiển thị 5 sự kiện gần nhất.
- Chỉ sự kiện `stage.failed` (lỗi cuối cùng, hết retry) được gửi tới `N8N_EVENT_WEBHOOK_URL`. Worker tự báo lỗi về Telegram; WF-07 chỉ ack event, không báo lại (tránh tin nhắn trùng).
- `POST /api/episodes/{id}/stages/{stage}` (dùng bởi WF-02…08) chỉ nhận các stage **trước** duyệt content (`jobs.STAGES`), và từ chối nếu truyện chưa duyệt rights (409). `render_final` / `publish_*` chỉ được tạo bởi approval; `h_render_final` còn tự kiểm tra lần duyệt content gần nhất là `approved`.

## Approval gates

| Gate | Khi nào | Hệ quả |
|---|---|---|
| `rights` | Nhập truyện | Không thể `/create_episode` khi chưa duyệt. |
| `cost` | Provider trả phí | `ALLOW_PAID_API=false` → bị chặn hẳn ở `budget.reserve`. |
| `scene` | Nút ✅/🔁 từng cảnh | Duyệt hoặc tạo lại ảnh cảnh đó. Ảnh bị từ chối không bao giờ được tự gán lại cho cảnh đó (`scenes.rejected_asset_ids`). |
| Nghe duyệt audio | Audio gate cần người nghe (`ListeningReviewRequired`) | Episode chuyển `needs_listening_review`; cần ghi `listening_review.json` (người duyệt + ghi chú) gắn với hash file. |
| `content` | Sau preview 720p | Duyệt → render 1080p. |
| `publish` | Sau QC 1080p | Duyệt → `ready_manual` (vì `ALLOW_AUTO_PUBLISH=false`). |

## Scene planning (Visual Story Engine)

Ứng viên ranh giới = giữa khoảng lặng sau mỗi đoạn văn (timestamp audio thật). Điểm thưởng: đổi section +3, đổi bối cảnh +2, đổi dàn nhân vật +1, cuối đoạn +0.5. DP tối thiểu hoá `Σ((d−120)/25)² − Σ bonus` với ràng buộc cứng 45 ≤ d ≤ 150 s. Không khả thi → thêm ranh giới cấp câu. Scene liền kề tuyệt đối (end_i = start_{i+1}, phủ [0, T]).

## Bảo mật

- Worker API bind `127.0.0.1`, mọi `/api/*` cần `X-API-Key` (so sánh hằng thời gian). n8n editor bind `127.0.0.1:5678`.
- Telegram: allowlist ở cả n8n (WF-00) và API; tham số lệnh chỉ nhận số nguyên/từ khoá whitelisted; file truyện chỉ `.md/.txt` ≤ 2 MB.
- Không dùng `shell=True`; mọi subprocess là mảng tham số. Text truyện tới `say` qua file tạm (`-f`).
- `/api/stories/import` chỉ nhận đường dẫn bên trong `storage/stories` hoặc `samples/`.
- `.env` (chmod 600) và `storage/`, `n8n_data/` nằm trong `.gitignore`.

## Database

`migrations/001_init.sql`, `002_cost_purpose.sql`: stories, chapters, episodes, scripts, narration_chunks, scenes, visual_assets, asset_usage, jobs, job_events, approvals, provider_costs, publishing_records, telegram_state, telegram_updates — có FK, index, CHECK trạng thái.

- `003_publish_upload.sql`: `publishing_records` thêm `external_url`, `last_error`, `published_at` và các trạng thái upload (`uploading`, `published`, `uploaded_draft`, `failed`).
- `004_scene_rejected_assets.sql`: `scenes.rejected_asset_ids`.

Migration chạy bằng `python -m studio.cli migrate`; file đã áp dụng được ghi trong bảng `schema_migrations`.

## Storage

```
storage/stories/<slug>/            bản gốc truyện
storage/episodes/<id>/             episode_script.json, transcript_timeline.json, scene_plan.json,
                                   image_generation_queue.json, scene_assets_map.json, timeline.json,
                                   subtitles.srt, qc_report_<h>p.json, clips/<h>p/<hash>.mp4
storage/audio/cache/<xx>/<hash>.wav   cache chunk TTS
storage/audio/episodes/<id>/narration.wav
storage/assets/{characters,environments,scenes}/<slug>/<sha>.png
storage/previews/<id>/preview_720p.mp4
storage/output/<id>/youtube_1080p.mp4
storage/logs/telegram_outbox.jsonl
```
