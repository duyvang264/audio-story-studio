# Dùng Audio Story Studio từ điện thoại

Mac mini chạy nền 24/7. Bạn chỉ cần Telegram trên điện thoại để gửi truyện, duyệt preview và đăng YouTube.

```
Điện thoại (Telegram) ──► Telegram server ◄── bot trên Mac mini (long polling, không mở cổng nào)
                                                  │
                                    worker: kịch bản → giọng đọc → ảnh → video → QC
                                                  │
                         bạn duyệt trên Telegram ─┴─► upload YouTube (private) / TikTok (nháp)
```

**Cách khuyên dùng: Telegram polling.** Không cần n8n, không cần Docker, không cần địa chỉ public hay mở port.
Mac mini tự hỏi Telegram có tin mới không, nên bạn ở đâu cũng điều khiển được.
n8n là tuỳ chọn (mục 7), chỉ dùng khi bạn muốn tự sửa luồng bằng giao diện kéo-thả.

---

## 1. Tạo bot Telegram (5 phút)

1. Trên điện thoại, mở Telegram và chat với **@BotFather**. Gõ `/newbot`, đặt tên, rồi nhận **token** dạng `123456:ABC...`.
2. Chat với **@userinfobot** để lấy **User ID** của bạn (một dãy số).
3. Gửi `/setcommands` cho @BotFather, chọn bot của bạn, rồi dán nội dung sau để có menu lệnh:
   ```
   start - Menu
   newstory - Nhập truyện mới (file, link Drive hoặc dán chữ)
   drive - Lấy truyện từ Google Drive
   stories - Danh sách truyện và tiến độ nhập Drive
   story - Kiểm tra nội dung sẽ được đọc
   xong - Kết thúc dán truyện
   create_episode - Tạo tập và chạy pipeline
   status - Trạng thái
   preview - Gửi lại preview
   approve - Duyệt
   regen_scene - Tạo lại ảnh 1 cảnh
   publish - Đưa bản final đi đăng
   budget - Chi phí
   cancel - Huỷ tập
   ```

## 2. Điền `.env` trên Mac mini

Mở `~/dev/projects/audio-story-studio/.env` và sửa hoặc thêm các dòng:

```
TELEGRAM_BOT_TOKEN=<token từ BotFather>
TELEGRAM_ALLOWED_USER_IDS=<user id của bạn>      # chỉ ID này điều khiển được bot
TELEGRAM_NOTIFY_CHAT_ID=<user id của bạn>
TELEGRAM_MODE=polling
TELEGRAM_DRY_RUN=false

TTS_PROVIDER=piper
PIPER_MODEL=storage/models/piper/vi_VN-vais1000-medium.onnx
PIPER_SPEED=95
SUBTITLE_MODE=both
BGM_PATH=storage/assets/bgm/dark_ambient.m4a
BGM_VOLUME=0.10

IMAGE_PROVIDER=codex              # ảnh thật qua gói ChatGPT; để mock nếu chỉ thử
CODEX_IMAGEGEN_VERIFIED=true
```

## 3. Cho phép upload YouTube (một lần, khoảng 10 phút)

1. Vào https://console.cloud.google.com, tạo project (ví dụ `audio-story-studio`).
2. Vào **APIs & Services → Library**, tìm **YouTube Data API v3** và bấm **Enable**.
3. Vào **OAuth consent screen**: chọn *External*, điền tên app và email, ở mục **Test users** thêm Gmail của kênh YouTube.
4. Vào **Credentials → Create credentials → OAuth client ID**, loại **Desktop app**, rồi **Download JSON**.
5. Chép file về Mac mini:
   ```
   mkdir -p ~/dev/projects/audio-story-studio/storage/secrets
   mv ~/Downloads/client_secret_*.json ~/dev/projects/audio-story-studio/storage/secrets/youtube_client_secret.json
   ```
6. Thêm vào `.env`:
   ```
   PUBLISH_UPLOAD_ENABLED=true
   YOUTUBE_PRIVACY=private        # private | unlisted | public
   ```
7. Ngồi tại Mac mini (cần trình duyệt), chạy:
   ```
   cd ~/dev/projects/audio-story-studio && .venv/bin/python -m studio.cli youtube-auth
   ```
   Đăng nhập đúng tài khoản kênh và bấm *Continue* qua cảnh báo "app chưa được xác minh" (vì app là của bạn).
   Terminal báo `OK: token lưu tại ...` là xong.

> Với app ở chế độ *Testing*, Google hết hạn token sau 7 ngày. Để không phải đăng nhập lại, bấm **Publish app**
> ở OAuth consent screen. Bạn vẫn là người dùng duy nhất; scope upload không bắt buộc Google xét duyệt khi dùng cho riêng mình.
> Quota mặc định: 10.000 đơn vị/ngày, mỗi lần upload tốn khoảng 1.600, tức khoảng 6 video/ngày.

TikTok (tuỳ chọn): tạo app ở developers.tiktok.com (Login Kit + Content Posting API, scope `video.upload`),
điền `TIKTOK_CLIENT_KEY/SECRET/REDIRECT_URI` vào `.env`, rồi chạy `.venv/bin/python -m studio.cli tiktok-auth`.
TikTok phải xét duyệt app; video được gửi vào hộp nháp và bạn tự bấm đăng.

## 4. Bật chạy nền 24/7

```
cd ~/dev/projects/audio-story-studio
.venv/bin/python -m studio.cli check-env     # kiểm tra: telegram, piper, codex, youtube_token...
scripts/services.sh install                  # worker + bot Telegram, tự khởi động lại khi lỗi hoặc reboot
scripts/services.sh status
scripts/services.sh logs telegram            # xem log bot (Ctrl+C để thoát)
```

Không cho Mac mini ngủ: **System Settings → Energy** → bật *Prevent automatic sleeping when the display is off*
và *Start up automatically after a power failure*. Hoặc chạy lệnh `sudo pmset -a sleep 0 disksleep 0 autorestart 1`.

Sau khi sửa `.env`, chạy lại `scripts/services.sh install` để dịch vụ đọc cấu hình mới.

## 5. Quy trình trên điện thoại

| Bước | Bạn gửi | Bot trả |
|---|---|---|
| 1 | `/newstory` | Hướng dẫn gửi truyện |
| 2 | Dán truyện thành nhiều tin nhắn (dòng đầu là tên truyện, chương bắt đầu bằng `## `), rồi gõ `/xong`. Hoặc gửi file `.md`/`.txt` | "Đã nhập truyện #12…" |
| 3 | `/approve rights 12` | Xác nhận bạn có quyền dùng nội dung |
| 4 | `/create_episode 12` | Pipeline chạy (khoảng 5–10 phút cho 5 phút truyện, lâu hơn nếu dùng ảnh Codex) |
| 5 | (đợi) | **Video preview** kèm nút ✅/🔁 cho từng cảnh |
| 6 | Bấm 🔁 ở cảnh không ưng | Chỉ cảnh đó được tạo lại, rồi gửi preview mới |
| 7 | Bấm **✅ Duyệt tập** | Render 1080p + TikTok 9:16, chạy QC |
| 8 | (đợi) | Video TikTok + nút **📤 Duyệt xuất bản** |
| 9 | Bấm 📤 | Upload YouTube (private) và TikTok (nháp); bot gửi link `youtu.be/...` |
| 10 | Mở app **YouTube Studio** trên điện thoại | Xem lại, chuyển sang Public khi sẵn sàng |

Lệnh khác: `/status`, `/status <tập>`, `/preview <tập>`, `/budget`, `/cancel <tập>`, `/publish <tập>`.

Mẹo viết truyện trên điện thoại:
```
Tên Truyện Của Bạn

## Chương 1: Đêm Sương

Đoạn văn thứ nhất...

Đoạn văn thứ hai...

***

(dấu *** = chuyển cảnh)

## Chương 2: ...
```
Muốn nhân vật và bối cảnh có ảnh nhất quán, thêm phần đầu YAML như file mẫu `samples/ngon-den-cuoi-cung-cua-vuc-tro.md`
(có thể soạn sẵn thành mẫu trong app ghi chú để dán).

## 6. An toàn

- Bot chỉ nghe lệnh từ `TELEGRAM_ALLOWED_USER_IDS`; người khác nhắn sẽ bị từ chối và ghi log.
- Không có bước nào tự đăng khi bạn chưa bấm duyệt. YouTube mặc định **private**, TikTok chỉ vào **nháp**.
- Token YouTube/TikTok nằm trong `storage/secrets/` (quyền 600, không commit git). Thu hồi quyền ở
  https://myaccount.google.com/permissions nếu mất máy.
- Polling không mở port nào trên Mac mini nên không có gì lộ ra Internet.

## 7. (Tuỳ chọn) n8n: sửa luồng bằng giao diện và đưa webhook ra Internet

Chỉ cần nếu bạn muốn chỉnh workflow bằng kéo-thả. **Không chạy cùng lúc** Telegram polling và WF-00
(Telegram chỉ cho phép một trong hai: webhook hoặc polling).

1. Khởi động Docker Desktop, rồi chạy:
   ```
   cd ~/dev/projects/audio-story-studio
   scripts/services.sh install --with-api          # Worker API 127.0.0.1:8765 cho n8n
   docker compose --env-file .env up -d
   scripts/n8n_import.sh                           # import 8 workflow WF-00…WF-07
   ```
2. Mở http://localhost:5678 (chỉ trên Mac mini), tạo tài khoản owner.
3. Vào **Credentials**:
   - **ASS Webhook Auth**: kiểu *Header Auth*, Name `X-API-Key`, Value = `API_KEY` trong `.env`.
   - **Audio Story Bot**: kiểu *Telegram API*, Access Token = `TELEGRAM_BOT_TOKEN`.
4. Mở từng workflow, kiểm tra credential đã gắn, rồi bấm **Publish** (hoặc chạy
   `docker compose exec n8n n8n publish:workflow --id=AudioStoryWF0100`…).
5. **Đưa webhook ra Internet (bắt buộc cho WF-00, vì Telegram cần HTTPS)**: dùng Cloudflare Tunnel và **chỉ mở
   đường `/webhook/`**, không mở trình soạn n8n:
   ```
   brew install cloudflared
   cloudflared tunnel login                       # cần tài khoản Cloudflare + 1 tên miền
   cloudflared tunnel create audiostory
   cloudflared tunnel route dns audiostory n8n.ten-mien-cua-ban.com
   ```
   File `~/.cloudflared/config.yml`:
   ```yaml
   tunnel: audiostory
   credentials-file: /Users/<bạn>/.cloudflared/<tunnel-id>.json
   ingress:
     - hostname: n8n.ten-mien-cua-ban.com
       path: ^/webhook/
       service: http://localhost:5678
     - service: http_status:404                    # mọi đường khác (editor, API) bị chặn
   ```
   Thêm `N8N_WEBHOOK_URL=https://n8n.ten-mien-cua-ban.com/` vào `.env`, đặt `TELEGRAM_MODE=n8n_webhook`, gỡ dịch vụ bot
   polling (`launchctl bootout gui/$(id -u)/com.audiostorystudio.telegram`), rồi `docker compose up -d` và
   `cloudflared tunnel run audiostory`.
6. Kiểm tra từ điện thoại bằng `/start` với bot.

Muốn quay lại polling: tắt WF-00 trong n8n, đặt `TELEGRAM_MODE=polling`, rồi chạy `scripts/services.sh install`.

## 8. Nhập truyện từ Google Drive trên Telegram

Sau khi đã chạy `drive-auth` trên Mac một lần, gửi trực tiếp link tài liệu cho bot hoặc:

```
/drive https://docs.google.com/document/d/ID_TAI_LIEU/edit
/stories
/story ID_TRUYEN
/approve rights ID_TRUYEN
/create_episode ID_TRUYEN
/status
```

Bot đưa việc tải Drive vào hàng đợi worker, trả lời ngay và gửi bản kiểm tra khi tải xong.
`/stories` cho biết tiến độ hoặc lỗi Drive; `/story` hiển thị số mục, số ký tự, thời lượng ước tính
và đoạn đầu sẽ được đọc. Đây là kiểm tra cấu trúc, không phải phân tích/biên tập bằng AI.
Truyện chưa được duyệt quyền sử dụng sẽ không được tạo tập.

Với tài liệu có phần `KỊCH BẢN TẬP 01` và các tiêu đề `Cảnh 01…`, hệ thống chỉ lấy các cảnh liên tiếp
ngay sau phần kịch bản; bỏ giới thiệu, bí mật mùa, danh sách tập và ghi chú biên tập khỏi giọng đọc.
Nếu có nhiều kịch bản tập hoặc không nhận diện rõ phần lời kể, bot yêu cầu bạn tách tài liệu.
Các ghi chú nằm bên trong một cảnh vẫn là nội dung cảnh, nên cần xem bản nguồn trước khi duyệt.
Nguồn gốc vẫn được lưu đầy đủ ở file tải về. Tài liệu sửa trên Drive không tự ghi đè truyện đã import;
để sản xuất bản sửa, hãy tạo tài liệu mới có ID khác.

Chỉ cần polling và worker chạy nền; không bắt buộc n8n. Sau khi cập nhật code:

```zsh
cd ~/Dev/Projects/audio-story-studio
scripts/services.sh install
scripts/services.sh status
```

Nếu `IMAGE_PROVIDER=mock`, preview dùng ảnh thử. Đổi sang nhà cung cấp ảnh thật sau khi kiểm tra nhà cung cấp đó.
Audio dùng `TTS_PROVIDER` hiện có. Không cần upload YouTube/TikTok để tạo preview và duyệt nội dung.
