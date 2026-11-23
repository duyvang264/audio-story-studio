# scripts/oneoff

Script dùng một lần hoặc thử nghiệm, gắn với một truyện/tập cụ thể (ví dụ "Ngôi Làng Không Có Người Chết", tập 1 và tập 8). Chúng không thuộc pipeline chính và có thể đã cũ so với code trong `studio/`.

Chạy từ thư mục gốc dự án:

```sh
PYTHONPATH=. .venv/bin/python scripts/oneoff/<ten_script>.py
```

| Nhóm | Script | Ghi chú |
|---|---|---|
| Chuẩn bị bộ truyện | `prepare_ngoi_lang_v4.py` | Tạo bộ sản xuất V4.1 trong `storage/production/` |
| Dựng lại tập | `rebuild_ngoi_lang_episode1.py`, `remix_ngoi_lang_suspense.py`, `expand_episode8_visuals.py`, `create_expressive_episode_audio.py` | Gắn cứng tập, dàn nhân vật, thư mục output |
| Kiểm tra phát âm | `audit_episode8_pronunciation.py` | Cần extra `audit` (faster-whisper, soxr) |
| Thử giọng / sửa audio | `try_free_voices.py`, `try_character_voices.py`, `try_audio_repairs.py` | Cần VieNeu (venv `storage/tools/vieneu/venv`) |

Công cụ dùng thường xuyên nằm ở `scripts/`: `services.sh`, `n8n_import.sh`, `gen_n8n_workflows.py`.

Nếu logic trong một script ở đây cần dùng lại, hãy đưa vào `studio/` (kèm test) thay vì copy script.
