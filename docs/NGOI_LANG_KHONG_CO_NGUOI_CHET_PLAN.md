# Kế hoạch sản xuất — Ngôi Làng Không Có Người Chết

Cập nhật 23/11/2026 theo **DOCX V4.0 trọn bộ 12 tập** do người dùng trực tiếp chỉ định. Đây là nguồn ưu tiên cho kế hoạch hiện tại, thay cho Bible V2 20 tập. Phạm vi lần này: kiểm tra tài liệu, đối chiếu và cập nhật kế hoạch; chưa sửa kịch bản gốc, nhập lại database, tạo audio hoặc xuất bản.

## Tiến độ thực thi theo yêu cầu tiếp theo

**Revision soundtrack theo yêu cầu tăng giật gân:** bản mới tại `storage/output/8/v41-rebuild-1/suspense-v2/`, thêm 11 điểm nhấn theo timestamp chunk: nhạc pulse dồn, riser, ngắt nền ngắn, boom/stinger và heartbeat chọn lọc. Giữ nguyên lời đọc và bitstream hình hai định dạng; có nhạc/master/QC mới, không tái sử dụng QC encoded cũ. Cả long/short đạt QC số, vẫn chờ nghe duyệt nguồn; không xuất bản. Cue và số đo so sánh nhạc lưu trong `sound_direction.json`, `music_before_after.json`, `run_report.json` tại output mới.

**Cập nhật sau yêu cầu dựng lại:** đã xuất tập 01 V4.1 tại `storage/output/8/v41-rebuild-1/34e1897a08152ba5/`: 103 đoạn đọc, 35 shot từ 12 ảnh, long 1080p và short 9:16. Audio thực dài 552,780 giây (~9:13), không kéo dài cho đủ mục tiêu 10–12 phút. Long AAC đo −16,02 LUFS/−2,07 dBTP, short −14,13 LUFS/−2,14 dBTP; cả hai đạt QC số. Giọng nguồn vẫn cần nghe duyệt, gồm ba hum candidate chưa chắc chắn; cổng xuất bản của cả hai vẫn `NEEDS_LISTENING_REVIEW`. Đã xem khung mẫu long/short, xác minh hash lời nguồn/master, giữ nguyên DOCX/database/bản cũ, không gọi paid API hoặc xuất bản. Xem `README.md` và `run_report.json` tại output. Các ghi nhận “chưa tạo audio” dưới đây là trạng thái lịch sử ở bước chuẩn bị, không phải trạng thái tập 01 hiện tại; tập 02–12 vẫn chưa dựng.

Đã chạy khâu chuẩn bị bằng `PYTHONPATH=. .venv/bin/python scripts/prepare_ngoi_lang_v4.py`. Bộ sản xuất ở `storage/production/ngoi-lang-khong-co-nguoi-chet/v4.1-production-draft-1-34a2134fa1fa/`, có `PROGRESS.md`, `manifest.json`, `series_config.json` và 12 thư mục tập. Mỗi tập có `narration.txt`, `story.md` nhập được bằng parser hiện có, `changes.json`, `changes.diff`, `production_status.json`.

Đã bỏ nhãn không dành để đọc, ghi chú V2 lọt vào tập 01 và đoạn trùng nguyên văn dài; bổ sung cầu nối Sen/bình đất tập 01, sửa lượt thoại thiếu tập 02, phân biệt chuông vật lý tập 04, sửa hành động hook tập 09 và rút trình tự tiễn biệt lặp tập 12. Các thay đổi là **đề xuất V4.1**, giữ đầy đủ dấu vết trước/sau, không đổi DOCX gốc hoặc giả định đã duyệt canon. Các vấn đề chưa giải quyết lưu trong `review_items` của từng tập.

Đã kiểm tra đủ 12 tập qua parser nhập hiện có, hash nguồn không đổi, kết quả tái chạy giống nhau và không còn đoạn dài trên 120 ký tự trùng nguyên văn giữa các bản lời đọc. Xem `validation_report.json`. Chưa đo được thời lượng vì chưa tạo TTS. Chưa nhập database, duyệt quyền, tạo audio/video hoặc thay cấu hình production. Cấu hình đích 660 giây (khoảng 10–12 phút) chỉ lưu riêng cho series, không đổi mặc định dự án.

Bước kế tiếp theo thứ tự: giải quyết `review_items` và khóa revision/cast → tạo audio tập 01 bằng nguồn mới → đo thời lượng/mix/QC/nghe duyệt → triển khai lần lượt các tập còn lại. Không coi khâu chuẩn bị hoàn tất là đã hoàn thành sản xuất 12 tập.

## 1. Nguồn đã kiểm tra

- File: `document/Ngoi_Lang_Khong_Co_Nguoi_Chet_Tron_Bo_12_Tap.docx`.
- Tiêu đề trong file: “TRUYỆN AUDIO NGUYÊN TÁC • BẢN TRỌN BỘ 12 TẬP”, “Bản biên tập V4.0 • 22/11/2026 • Một tài liệu duy nhất”.
- SHA-256: `34a2134fa1fab75c35bc1183716f01df0e91f27631775aa5eb51df57dddd1780`.
- Đã trích đọc 71.007 ký tự văn bản, xác nhận đủ 12 phần `TẬP 01` đến `TẬP 12`, mỗi tập có hook mở, nội dung và hook kết/dư âm. Đây là bản có nội dung truyện, không chỉ outline.
- Mục tiêu ghi trong file: **10–12 phút/tập**, phải đo bằng lời đọc thật; không còn áp mục tiêu mặc định 20 phút của engine cho series này.
- Snapshot đọc: `storage/research/ngoi-lang-drive/local_12_tap.txt`; số liệu và đoạn trùng: `storage/research/ngoi-lang-drive/local_v4_audit.json`.

Kết luận trước đây coi file 12 tập là bản cũ chỉ dựa tên file là không đủ căn cứ và được thay thế bởi kết quả đọc nội dung V4 này. Chưa xác minh file máy trùng byte với bản trong thư mục Drive mới vì Drive trả 404; không cần chờ Drive để lập kế hoạch từ file máy được người dùng chỉ định. ID V2 cũ nằm trong thùng rác không còn là nguồn sản xuất mới.

## 2. Thay đổi so với kế hoạch V2

| Hạng mục | Kế hoạch trước | Kế hoạch hiện tại |
|---|---|---|
| Nguồn ưu tiên | Bible V2 + tập 01, tập 02 riêng | Một DOCX V4 gồm 12 tập |
| Quy mô | Outline 20 tập | Có văn xuôi 12 tập |
| Thời lượng | Khoảng 20 phút/tập | Mục tiêu 10–12 phút/tập; đo từng tập |
| Hai giọng Minh | Cơ chế còn để mở | Hai hướng/phần bản ngã từ biến cố 2008; Duy là phần danh tính bị giữ |
| 43 bình | Cần khóa quan hệ với bình Minh | 43 bình gốc 1926; bình mang tên Minh được dùng lại/thay nhãn năm 2008 |
| Người cuối danh sách | Chưa chốt trong plan | Lê Thị Nguyệt, cần sửa manh mối tên mâu thuẫn trước khi khóa |
| Kết mùa | Tập 20 | Tập 12 tiễn biệt, kết thúc trọn vẹn; không ép cliffhanger ma mới |
| Ưu tiên | Mở rộng tập 1 lên 20 phút | Làm sạch V4, tách bản lời đọc, đo thời lượng rồi mới TTS |

## 3. Danh sách tập và độ dài bản thảo

Số dưới đây là **đơn vị tách theo khoảng trắng**, bao gồm tiêu đề/nhãn trong phần tập, không phải số từ ngôn ngữ học hay thời lượng TTS. Chưa loại đoạn trùng nên không dùng để cam kết đủ 10–12 phút.

| Tập | Tên theo V4 | Đơn vị khoảng trắng |
|---|---|---:|
| 01 | Lá Thư Của Người Đã Chết | 2.102 |
| 02 | Người Đứng Ngoài Cửa | 2.145 |
| 03 | Đám Tang Không Có Người Chết | 1.221 |
| 04 | Tiếng Chuông Thứ Mười Ba | 1.093 |
| 05 | Bốn Mươi Ba Bình Đất | 1.071 |
| 06 | Chợ Phiên Người Âm | 1.098 |
| 07 | Cuộn Băng Năm 2008 | 1.064 |
| 08 | Đêm Dịch Một Trăm Năm Trước | 1.108 |
| 09 | Người Cha Dưới Đáy Giếng | 1.127 |
| 10 | Kẻ Giữ Chuông | 1.107 |
| 11 | Người Thứ Bốn Mươi Ba | 1.474 |
| 12 | Đám Tang Đầu Tiên Sau Trăm Năm | 923 |

Tập 1–2 dài đáng kể so với phần giữa; tập 12 ngắn nhất. Cần cân lại bằng nhịp kể/nội dung, không đồng loạt giảm tốc hoặc chèn im lặng để đạt phút. Tập 3 đổi tên so với outline “Không Có Quan Tài” trước đây: dùng đúng tên V4 khi xuất metadata.

## 4. Audit biên tập — chưa sẵn sàng đọc nguyên xi

### P0: nội dung lọt vào lời đọc, cầu nối và timeline

1. **Tập 01 có câu biên tập cũ nằm giữa truyện:** “Tập 1 là bản thảo V2, không khẳng định đã đủ 20 phút…”. Loại khỏi narration; giữ trong lịch sử biên tập nếu cần. Không đọc mục lục, nhãn HOOK/NỘI DUNG, số phiên bản hay lưu ý biên tập.
2. **Tập 01 thiếu giới thiệu Sen trong bản hiện tại:** chuyển từ nhật ký sang “Rồi từ dưới giếng vang lên giọng mẹ… Sen lắc đầu…”, nhưng trước đó chưa dựng cảnh Sen xuất hiện và gặp Minh. Cần bổ sung cầu nối nhân vật/vị trí. Sau chuông, đoạn “Trên thân bình…” cũng cần nối việc bà đem bình vào và ai đang cầm bình.
3. **Tập 02 thiếu lượt thoại:** sau “Một khoảng im lặng” đi vào “Bà chắc chứ?” / “Hai.” nhưng thiếu câu hỏi và sự xuất hiện của ông Cẩn để người nghe hiểu. Câu “Nhưng không nhìn thấy bóng của ông Cẩn.” thiếu chủ thể/cầu nối. Sửa theo ý tác giả, không tự bỏ vì tưởng chỉ là khoảng ngắt.
4. **Tập 03 đảo mốc:** mở bằng sáng hôm sau/cụ Mão rồi quay về ông Cẩn ở cửa, lại “Sáng hôm sau”. Cần đánh dấu hồi tưởng hoặc sắp lại thành một trình tự rõ; gương vỡ xuất hiện nhưng quá trình tìm ra gương đã bị lược ở bản tập 02 hiện tại.
5. **Tập 04 dùng đạo cụ/cảnh chưa dựng:** “đồng xu giữ tôi nhớ mình là ai”, “bà… thức chờ tôi ở cửa hầm” trước khi tập này kết bằng tìm cửa hầm. Cần giới thiệu đồng xu hoặc bỏ dẫn chiếu không còn trong V4; chỉnh thứ tự hầm/đêm/sáng.
6. **Tập 05 có hai lối xuống hầm** (khóa bệ thờ cuối tập 04 và vòng sắt sau bếp ở tập 05). Chốt là cùng lối hay hai lối; tránh kể lại lần khám phá như lần đầu.
7. **Tập 06 luật chợ:** lời giới thiệu nói mua bán bằng ký ức, sau đó Minh đổi đồng hồ vì “không chứa ký ức”. Cần giải thích ngoại lệ hoặc sửa giao dịch; không biến việc tránh cái giá thành lỗ hổng thuận tiện.
8. **Tập 09 hook nói “Tôi xuống giếng” nhưng thân truyện nói không bước xuống, chỉ hạ thiết bị**; cuối có “trở về bờ”/“xuống… lần nữa”. Thống nhất hành động thực, phân biệt trải nghiệm ký ức với xuống giếng bằng cơ thể.

### P0: lặp và mâu thuẫn canon

- Phát hiện tự động **9 đoạn dài trên 120 ký tự trùng nguyên văn**: 5 đoạn ở tập 04–05 (bà quên Minh, ba bát cơm, ghi âm và tờ giấy dán cửa); 4 đoạn ở tập 06–09 (giấy khai tử Sen, tuổi thơ, tên “Lê Thị Sên”, lời hứa được lựa chọn). Đây là ngưỡng đếm cụ thể, không phải toàn bộ lặp ý. Chỉ giữ một lần đầy đủ, lần sau nhắc ngắn với phát hiện mới.
- Tập 03 kể xong vào nhà từ/gia phả rồi lại nhờ Sen tìm đường vào nhà từ; tập 07 lặp mở băng/thiết bị; hook đầu và đầu nội dung nhiều tập cùng kể lại một sự kiện. Rút hoặc đánh dấu hồi tưởng, tránh làm người nghe tưởng diễn ra hai lần.
- Tập 12 tiễn Duy rồi rời làng/gọi mẹ, sau đó quay về Duy/Sen/bà trước lễ và lại rời làng/gọi mẹ. Sắp lại duy nhất: chuẩn bị → đọc tên/đồng thuận → tiễn từng người → lễ kết → qua cầu/gọi mẹ → dư âm bản ghi bố.
- Tên thứ 43 có manh mối “Lê Thị Sên” (tập 06/09), nhưng tập 11–12 chốt “Lê Thị Nguyệt”. Nếu Sên là đọc sai phải có sửa sai và bằng chứng rõ; nếu không, thống nhất về Nguyệt. Không vô tình biến Sen thành người chết năm 1926.
- Tập 07 người gõ chuông tháo nón là **Trạch**; tập 10 hook kết nói dưới nón không có khuôn mặt, là chức phận chắp ký ức. Phân biệt người thực gõ 12 tiếng với hiện thân/chức phận siêu nhiên; dựng chuyển tiếp trước reveal, không coi hai mô tả mặc nhiên tương thích.
- Chuông 13 có câu “không ai nghe thấy” ở tập 04 trong khi tập 01 vang trong đầu. Chốt phân biệt không có âm thanh vật lý/máy không thu với trải nghiệm chủ quan của người nghe; không thêm tiếng bell thứ 13 thông thường để minh họa.
- Tên mẹ gọi Minh đã mất không tự hồi phục. Tập 11 nói ký ức trả về “nếu còn có thể”: phải giữ ngoại lệ này nhất quán đến hết mùa; đọc lại thông tin không đồng nghĩa có lại cảm giác/ký ức.
- 43 bình gốc, người được giữ thêm sau 1926 (Sen 1998, Minh 2008, con Cẩn 1959) và thay nhãn cần một bảng quy tắc sức chứa/đánh đổi. Không tăng thành 44 bình hoặc tự bổ sung số người khi chưa biên tập chốt.

## 5. Canon làm việc theo V4

Minh sinh 1997, ngừng thở năm 2008 lúc 11 tuổi, hiện 29 tuổi năm 2026. Nguyễn Văn Hòa để lại băng và bằng chứng; phần còn lại dưới giếng không phải người cha nguyên vẹn hồi sinh. Bà Tư Lành và Sen có ý chí, được lựa chọn lời tiễn, không chỉ là đạo cụ cho Minh giải cứu.

Mượn Hơi là nghi lễ hư cấu trì hoãn tiễn biệt, giữ hình bóng/hơi thở/thói quen, trả bằng ký ức. 43 bình gốc thuộc 1926; nhãn Minh thay vào năm 2008. Duy là phần bản ngã/danh tính bị giữ, không phải một người sống độc lập vô hạn hay “đồ giả” mặc định. Tên cuối là Lê Thị Nguyệt theo hồi kết hiện tại, còn manh mối tên cần sửa như audit.

Máy thu không là chân lý: Minh đối chiếu hai thiết bị, vật chứng và lời kể. Phát hiện thật không được suy ra chỉ từ một giọng siêu nhiên. Địa danh/lịch sử/nghi lễ là hư cấu. Những điểm audit phía trên chưa khóa không được đưa thành canon được duyệt chỉ vì đã ghi vào Plan.

## 6. Trình tự triển khai

### Bước 1 — Tạo bản sản xuất V4.1 riêng

Giữ nguyên DOCX V4 và SHA. Tách 12 tập thành bản thảo sản xuất có nguồn, nhãn version và bảng thay đổi; không sửa file gốc. Loại rác biên tập, xử lý lặp/cầu nối/canon theo mục 4. Giữ hook tác giả, chỉ chỉnh trùng/logic cần thiết; không tự chèn ba hook thay thế hoặc thêm teaser vào bản kết trọn vẹn. Chỉ sau khi lời đã khóa mới nhập revision riêng qua quy trình hiện có; không dựa vào import cùng slug để tự cập nhật DB vì importer không ghi đè nội dung đã tồn tại.

Từng tập cần: script lời đọc, danh sách scene theo nội dung, speaker/cảm xúc, bảng tên/phát âm, canon đã lộ/chưa lộ, hook mở/kết và dự kiến SFX. ID episode 8 đang là tập 01 cũ, không phải tập 08 của series; lập bảng series episode number ↔ database episode ID để tránh gắn nhầm.

### Bước 2 — Chốt độ dài 10–12 phút theo lời thật

Ghi override thời lượng series trong cấu hình/job phù hợp để không dùng mặc định story-engine 20 phút. Đo take thật từng tập sau khi sửa lặp; cập nhật hook/title/story/climax/kết theo timeline thực. Không ép tỷ lệ 20 phút vào mọi tập, không coi khoảng 11:07 của audio V2 cũ là thời lượng V4. Tập quá ngắn bổ sung cảnh/hành động có hậu quả sau biên tập, không tự kéo giọng/chèn nhạc cho đủ phút; tập dài cắt lặp trước khi tăng tốc.

### Bước 3 — Audio tập 01 mới trước, rồi mới nhân rộng

Giữ cast đã chọn: Thiện Minh dẫn truyện/Minh; Ngọc Linh bà; Đoan Trang Sen; Ngọc Huyền mẹ; Thanh Bình cha; Thiền Tâm Đức tài xế/người lạ; Trúc Ly trẻ nhỏ. Lập thêm lựa chọn cast rõ cho Cẩn, Trạch, Duy, Nguyệt và dân làng trước tập tương ứng; chưa coi đã chọn đủ giọng mới.

So từng câu V4.1 với 106 đoạn bản diễn V2. Chỉ tái dùng take trùng lời, đúng speaker/cảm xúc và đã nghe duyệt; đổi câu/vị trí/ngữ cảnh thì cập nhật timing hoặc tạo lại. Ánh xạ performance plan cũ sang `plan_chunks` trước khi dùng chỉ số regenerate; không truyền chỉ số cũ trực tiếp. Lưu revision riêng, không chồng lên audio cũ.

Lời kể đi từ lý trí sang bất an, ấm/nghẹn khi gặp bà, gấp khi bị đe dọa và chậm khi tiễn biệt. Giữ khoảng nghỉ, không làm mọi nhân vật thì thầm hoặc dùng méo tiếng thay diễn xuất. VieNeu miễn phí/local không bảo đảm tự điều khiển cảm xúc; take không đạt phải nghe/chọn lại. Kiểm tên Minh/Hòa/Sen/Trạch/Duy/Nguyệt, năm 1926/1998/2008, số 43 và mã file. ASR chỉ tìm ứng viên sai, không tự thông qua phát âm.

### Bước 4 — Soundscape, visual và render

Tension 0–5 có khoảng hạ theo từng tập; tập 12 ưu tiên mất mát/nhẹ nhõm và dư âm, không ép jumpscare cuối. Tạo cue từ cảnh và narration mới, ưu tiên lời thoại; duck music/SFX, giữ im lặng được chỉ định. Không mặc định phát chuông 13 như âm thanh thu được. Catalog bản quyền/checksum/attribution; dùng fallback local khi thiếu asset.

Tái sử dụng ảnh đúng nội dung từ bản cũ (12 ảnh/22 shot là tài nguyên tham khảo, không phải storyboard V4). Lập shot list mới, đánh dấu ảnh thiếu; tăng mật độ ở hook/phát hiện/cao trào theo thời lượng lời thật. Không tạo ảnh trả phí. Phụ đề mới từ script đã khóa; timeline/short window cũ phải tính lại.

### Bước 5 — QC và triển khai các tập tiếp theo

Source QC → repair an toàn → narration QC → stem mix → premaster QC → master QC → render → QC AAC/AV thật cho từng long/short → nghe duyệt → sẵn sàng duyệt xuất bản thủ công. Long mặc định −16 ±1 LUFS, true peak ≤−1,5 dBTP; short giữ target riêng −14 LUFS. A/V lệch mục tiêu ≤100 ms, không mất lời cuối hoặc chunk. Không bỏ review nguồn chỉ vì video đạt chỉ số.

Sau khi tập 01 mới đạt chất lượng, triển khai tập 02 và tiếp tục theo thứ tự 12 tập đã khóa. Không tạo hàng loạt trước khi sửa các lặp/canon xuyên mùa. Native Python worker và n8n giữ hợp đồng hiện có, retry hữu hạn; không tự deploy/publish.

## 7. Tài nguyên cũ và điều kiện bàn giao

Bản thử V2 hiện có: `storage/output/8/audio_upgrade/007dc8e3b78b6081/`, audio khoảng 11:07, long/short đạt QC số nhưng `NEEDS_LISTENING_REVIEW`. Đây là **baseline cũ**, không phải bản sản xuất khớp DOCX V4. Không sử dụng báo cáo QC/hash cũ để duyệt bản mới.

Mỗi tập bàn giao script version/nguồn, narration raw/clean, stems/master, cue/timeline/subtitle, video long/short nếu có, QC JSON/Markdown/metrics/flagged segments/review clips và quyết định nghe duyệt đúng hash. Không thiếu lời, không còn lỗi nghiêm trọng, không tự coi bản quyền đã duyệt chỉ vì tài liệu ghi “nguyên tác”.

Chi phí lần kiểm tra/cập nhật này: 0 đồng API/asset. Ưu tiên VieNeu/local/cache, giữ trần ứng dụng 500.000 đồng/tháng; không gọi dịch vụ trả phí nếu chưa được cho phép. Xem [hướng dẫn engine](AUDIO_ENGINE.md) và [báo cáo triển khai kỹ thuật](AUDIO_UPGRADE_REPORT.md). Lần này chỉ kiểm tra tài liệu và sửa kế hoạch; không chạy lại 138 test kỹ thuật vì không đổi mã engine.
