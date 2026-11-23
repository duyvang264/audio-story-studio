# PLAN — Audio Story Studio

Cập nhật: 2026-11-23 · Horror Audio Engine đã triển khai và kiểm thử; nội dung bên dưới giữ lại kết quả audit lịch sử, không phải kiểm tra lại trạng thái dịch vụ hôm nay.

## Kế hoạch ưu tiên hiện tại — Ngôi Làng Không Có Người Chết

Đã chạy tách và chuẩn bị **12 bản thảo sản xuất V4.1**, kèm diff, trạng thái từng tập và cấu hình series riêng tại `storage/production/ngoi-lang-khong-co-nguoi-chet/v4.1-production-draft-1-34a2134fa1fa/PROGRESS.md`. Kiểm tra parser, tính tái lập và hash nguồn đạt; chưa tạo TTS hoặc duyệt các điểm canon còn mở. Xem kế hoạch chi tiết để phân biệt phần đã làm với phần còn lại.

Nguồn ưu tiên hiện tại là file người dùng chỉ định `document/Ngoi_Lang_Khong_Co_Nguoi_Chet_Tron_Bo_12_Tap.docx`: đã đọc nội dung, xác nhận **V4.0 trọn bộ 12 tập**, mục tiêu **10–12 phút/tập**. Kết luận trước coi file này là bản cũ chỉ từ tên file được thay thế. Drive mới vẫn chưa truy cập được, nhưng không cản trở lập kế hoạch theo bản máy đã đọc.

Xem [kế hoạch sản xuất cập nhật theo V4](docs/NGOI_LANG_KHONG_CO_NGUOI_CHET_PLAN.md): xử lý đoạn biên tập lọt vào lời đọc, lặp tập 04–05/06–09 và cầu nối/canon → tạo revision sản xuất riêng → đo lời thật theo mục tiêu 10–12 phút → TTS/sound/visual → QC file encoded → nghe duyệt → hàng chờ xuất bản thủ công. Outline V2 20 tập chỉ giữ làm lịch sử, không áp lên bản V4.

Audio tập 01 cũ khoảng 11:07 là bản V2, chưa khớp lời V4; cần đối chiếu từng take trước khi tái dùng. Bản render thử đạt QC số nhưng còn `NEEDS_LISTENING_REVIEW`; không đánh dấu đã sửa hết rè/sai từ hoặc sẵn sàng đăng. Bộ kiểm thử engine gần nhất: 138 đạt (không chạy lại khi chỉ sửa tài liệu). Không sửa DOCX gốc, deploy, tạo paid asset hoặc xuất bản trong lần cập nhật kế hoạch này.

## 0. Kết quả audit môi trường (Phase 0, đã kiểm tra thực tế)

| Thành phần | Trạng thái | Ghi chú |
|---|---|---|
| Mac mini M4, macOS 26.6.2 | OK | |
| `~/Projects/hac-am-tay-du` | Không động vào | Chỉ có thư mục `n8n/` rỗng. Dự án mới nằm ở `~/Projects/audio-story-studio`. |
| Python | 3.14.3 (venv), 3.15.2 hệ thống | Dùng 3.14 vì đủ wheel cho pydantic/psycopg. |
| PostgreSQL 14.17 (Homebrew) | OK | DB `audio_story_studio`, `audio_story_studio_test`. |
| FFmpeg 9.0.2 | OK, **thiếu libass/freetype** | Có zoompan, xfade, loudnorm, silencedetect, blackdetect, ebur128. Không có `subtitles`/`drawtext` → burn-in bằng overlay PNG (Pillow) + track mềm + `.srt`. |
| TTS tiếng Việt local | OK | macOS `say -v Linh` và **Piper** `vi_VN-vais1000-medium` (onnxruntime, đã cài, ~0,3 s/câu). Không có torch. |
| Phụ đề burn-in | OK (thay thế) | Overlay PNG vẽ bằng Pillow, không cần libass. |
| BGM | OK | `studio.cli make-bgm` sinh dark ambient 4 phút bằng FFmpeg (tự tạo, không bản quyền). |
| Codex CLI 0.169.0 | OK, đăng nhập bằng ChatGPT | `image_generation` = stable/true. |
| Codex ImageGen headless | **Đã xác minh hoạt động** | `codex exec` gọi tool built-in `image_gen` (không cần `OPENAI_API_KEY`), ảnh lưu ở `~/.codex/generated_images/<session>/`. ~40–60 s/ảnh, 1672×941. Provider loại `OPENAI_API_KEY` khỏi env để Codex không thể rơi vào đường CLI/API trả phí. Tốn quota gói ChatGPT, nên có giới hạn `CODEX_MAX_IMAGES_PER_DAY`. |
| Docker Desktop | Cài đặt, **daemon không chạy** | `open -a Docker` không khởi động được trong phiên này (cần thao tác GUI). |
| n8n | Không cài sẵn | Workflow được kiểm tra import bằng n8n CLI 2.48.7 (npm) — xem TASKS.md. |
| Telegram bot token | Chưa có | Bot chạy chế độ dry-run (ghi `storage/logs/telegram_outbox.jsonl`) cho tới khi cấu hình token. |

## 1. Quyết định kiến trúc

1. **Worker Python là engine thực thi; n8n là lớp điều phối/ingress.** Mọi bước nặng (TTS, ffmpeg, Codex) chạy native trên macOS vì cần `say`, VideoToolbox, `codex`. n8n (Docker) chỉ gọi Worker API qua HTTP + API key.
2. **Job queue bằng PostgreSQL** (`FOR UPDATE SKIP LOCKED`), idempotency key duy nhất, retry exponential backoff, timeout + thu hồi lock hết hạn.
3. **AUTO_CHAIN=true**: worker tự nối các stage. Mỗi WF-0x cũng có thể kích hoạt riêng một stage (idempotent theo `episode_id + run`).
4. **Scene boundary theo nội dung + timestamp audio thật** (DP trên ranh giới đoạn/câu), không theo số ký tự.
5. **Reuse trước, tạo sau**: asset matcher không bao giờ dùng ảnh có nhân vật/bối cảnh sai; chỉ tạo ảnh còn thiếu (≤4/tập).
6. **Render theo clip từng cảnh có cache** (hash ảnh + motion + frames + độ phân giải) → sửa 1 cảnh chỉ render lại 1 clip.
7. **Không có đường gọi API trả phí**: `ALLOW_PAID_API=false`, provider chưa có bảng giá bị coi là trả phí và bị chặn.

## 2. Phases

### Phase 0 — Audit & Planning ✅
Đã làm: audit môi trường (bảng trên), xác minh Codex ImageGen headless, PLAN/ARCHITECTURE/TASKS.

### Phase 1 — MVP End-to-End ✅
Telegram command → nhập truyện mẫu tự sáng tác → duyệt bản quyền → script → TTS → scene plan → asset match/generate → timeline → render 720p → QC → gửi preview (Telegram, dry-run) → duyệt → render 1080p → QC → publishing queue (chờ duyệt).
**Acceptance đã đạt** (`python -m studio.cli smoke --fresh`): xem TASKS.md mục "Kết quả kiểm thử".

### Phase 2 — Visual Automation (một phần đã có)
Có: Prompt Builder + style profile, Asset Manager/matcher, CodexImageProvider (đã xác minh), asset cache, regen từng cảnh, 4 motion preset, Budget Manager.
Còn: chạy pipeline hoàn chỉnh với `IMAGE_PROVIDER=codex` (mới thử 1 ảnh), ảnh tham chiếu nhân vật (character sheets) cho consistency, LocalImageProvider với một backend thật.

### Phase 3 — Production & TikTok (một phần)
Đã có: TikTok 9:16 tự chọn đoạn cao trào, phụ đề burn-in, BGM tự sinh, Piper TTS.
Đã có thêm: uploader YouTube/TikTok sau duyệt (mặc định tắt).
Còn: chạy upload thật (cần OAuth của bạn), thư viện BGM, dashboard tiến độ.

## 3. Rủi ro & giảm thiểu

| Rủi ro | Mức | Giảm thiểu |
|---|---|---|
| Quota ImageGen của gói ChatGPT không công bố/thay đổi | Cao | Giới hạn/ngày, tối đa 4 ảnh/tập, fallback mock, reuse thư viện. |
| Codex CLI thay đổi hành vi `exec`/đường dẫn ảnh | TB | Provider tìm `out.png` rồi fallback `generated_images`; `check-codex-imagegen` để kiểm tra lại sau mỗi lần nâng cấp. |
| Giọng `say` Linh chất lượng chưa tự nhiên | TB | Interface `TTSProvider` để thêm model local tốt hơn (VieNeu/F5-TTS-vi) khi có torch; cache theo model_version. |
| FFmpeg không có libass | Thấp | Đã giải quyết: overlay PNG vẽ bằng Pillow. |
| Telegram cần HTTPS cho webhook | TB | Chế độ `polling` không cần URL công khai; hoặc tunnel chỉ mở `/webhook/*`. |
| Video >50 MB không gửi được qua Bot API | TB | Preview CRF 28; nếu vượt ngưỡng gửi teaser 60 s + đường dẫn file. |
| Docker daemon không chạy | Thấp | Worker không phụ thuộc Docker; n8n có thể chạy bằng npm. |
