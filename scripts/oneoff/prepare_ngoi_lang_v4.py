"""Prepare isolated, auditable production drafts from the user's twelve-episode DOCX.

No model calls, database writes, rights approvals, audio synthesis or publication.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
from pathlib import Path

import yaml

from studio.drive import docx_text
from studio.story import parse_story_file

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'document/Ngoi_Lang_Khong_Co_Nguoi_Chet_Tron_Bo_12_Tap.docx'
REVISION = 'v4.1-production-draft-1'
LABELS = {'MỞ TẬP — HOOK', 'NỘI DUNG TRUYỆN', 'KẾT TẬP — HOOK', 'KẾT TRUYỆN — DƯ ÂM'}
CAST = {'narrator': 'Thiện Minh', 'minh': 'Thiện Minh', 'grandmother': 'Ngọc Linh',
        'sen': 'Đoan Trang', 'mother': 'Ngọc Huyền', 'father': 'Thanh Bình',
        'driver': 'Thiền Tâm Đức', 'child': 'Trúc Ly', 'double': 'Thiện Minh'}
REVIEW = {
    1: ['Nghe/đối chiếu diễn xuất; xác nhận cầu nối Sen và chiếc bình vừa bổ sung.'],
    2: ['Duyệt lượt thoại bổ sung khi ông Cẩn xuất hiện; khóa phạm vi đáp lời kích hoạt mất ký ức.'],
    3: ['Sắp lại hồi tưởng đêm ông Cẩn tới và sáng cụ Mão; giới thiệu gương vỡ trước dẫn chiếu.'],
    4: ['Giới thiệu/bỏ đồng xu chưa dựng; sửa mốc bà chờ cửa hầm trước lúc tìm ra hầm.'],
    5: ['Thống nhất lối xuống hầm với cuối tập 04; thay đoạn hồi tưởng đã bỏ bằng diễn biến mới nếu cần.'],
    6: ['Chốt ngoại lệ đổi đồng hồ tại chợ ký ức; giải thích manh mối tên đọc sai thay vì khẳng định tên cuối.'],
    7: ['Phân biệt Trạch là người gõ chuông thực với chức phận siêu nhiên tập 10.'],
    8: ['Duyệt cơ chế 43 bình gốc và những người được giữ thêm sau 1926.'],
    9: ['Duyệt lời sửa hook/hành động: đứng trên bờ, không thực sự trèo xuống giếng.'],
    10: ['Chốt chuyển tiếp Trạch có mặt và hiện thân không mặt; chỉnh mốc trời sáng/đêm cuối.'],
    11: ['Khóa Lê Thị Nguyệt với manh mối tên đọc sai; thống nhất lúc gọi tên và hoàn tất nghi lễ.'],
    12: ['Duyệt trình tự tiễn biệt đã rút lặp; không tự phục hồi ký ức tên mẹ gọi đã mất.'],
}


def prepare(source: Path, output: Path | None = None) -> Path:
    raw = source.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    text = docx_text(raw)
    starts = list(re.finditer(r'^## TẬP (\d+)\s*$', text, re.M))
    if [int(m.group(1)) for m in starts] != list(range(1, 13)):
        raise ValueError('Expected exactly twelve ordered episode headings')
    output = output or ROOT / 'storage/production/ngoi-lang-khong-co-nguoi-chet' / f'{REVISION}-{sha[:12]}'
    output.mkdir(parents=True, exist_ok=True)
    originals, entries, seen = {}, [], {}
    for index, start in enumerate(starts):
        number = index + 1
        body = text[start.end():starts[index + 1].start() if index + 1 < len(starts) else len(text)].strip()
        title, rest = body.split('\n', 1)
        title = title.removeprefix('## ').strip()
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', rest) if p.strip()]
        originals[number] = '\n\n'.join(p for p in paragraphs if p not in LABELS)
        cleaned, changes = [], []
        for p in paragraphs:
            if p in LABELS:
                continue
            if p.startswith('Tập 1 là bản thảo V2, không khẳng định đã đủ 20 phút.'):
                changes.append({'action': 'remove_editorial_note', 'original': p})
                continue
            if len(p) > 120 and p in seen:
                changes.append({'action': 'remove_exact_repetition', 'first_episode': seen[p], 'original': p})
                continue
            seen[p] = number
            cleaned.append(p)

        def replace(old: str, new: str):
            nonlocal cleaned
            matches = sum(p.count(old) for p in cleaned)
            if matches != 1:
                raise ValueError(f'Episode {number}: edit anchor occurs {matches} times: {old[:60]}')
            cleaned = [p.replace(old, new) for p in cleaned]
            changes.append({'action': 'editorial_proposal', 'original': old, 'replacement': new})

        if number == 1:
            anchor = 'Rồi từ dưới giếng vang lên giọng mẹ tôi:'
            replace(anchor, 'Gần nửa đêm, tôi thấy một ánh đèn dầu bên giếng đình. Người cầm đèn là Sen, bạn tôi hồi nhỏ. Cô vẫn trẻ đến khó tin. Tôi hé cửa sổ, vừa định gọi thì cô đưa ngón tay lên môi. Sen ra dấu cho tôi hướng máy thu về phía giếng. Tai trần chỉ nghe gió, còn trong tai nghe là những giọng người chồng lên nhau. Rồi từ dưới giếng vang lên giọng mẹ tôi:')
            replace('Trên thân bình dán giấy vàng viết tên Nguyễn Đức Minh,', 'Khi căn phòng có tiếng trở lại, bà nội bước vào, ôm một chiếc bình đất quấn chỉ đỏ. Bà đặt bình lên bàn trước mặt tôi. Trên thân bình dán giấy vàng viết tên Nguyễn Đức Minh,')
        elif number == 2:
            replace('Một khoảng im lặng.', 'Một khoảng im lặng. Ngoài cổng có thêm ánh đèn bão. Một giọng đàn ông già hỏi bà nội: “Trong nhà bà đang giữ mấy đứa trẻ của đêm năm ấy?” Bà đáp rất khẽ: “Hai.”')
            replace('Tôi nhận ra giọng ông từ buổi tối: ông Cẩn, người đàn ông đã đứng ngoài cổng khi tôi vừa ăn cơm xong. Trưởng làng.', 'Bà nội gọi người đàn ông ấy là ông Cẩn. Trưởng làng.')
            replace('Nhưng không nhìn thấy bóng của ông Cẩn.', 'Ánh đèn bão quét qua nền sân, nhưng tôi không nhìn thấy bóng của ông Cẩn.')
        elif number == 4:
            replace('Không ai nghe thấy tiếng chuông thứ mười ba.', 'Không máy nào thu được tiếng chuông thứ mười ba như một âm thanh thông thường.')
        elif number == 9:
            replace('Tôi xuống giếng để tìm bố,', 'Tôi tìm dấu vết của bố dưới giếng,')
            replace('Khi trở về bờ, tôi thấy tay mình run', 'Khi rời thành giếng, tôi thấy tay mình run')
            replace('Nếu anh xuống giếng lần nữa,', 'Nếu anh lại điều tra chiếc giếng,')
        elif number == 12:
            # Keep one farewell sequence; defer departure to the final afterglow.
            for prefix in ('Tôi bước qua cây cầu đá lúc mặt trời đã lên cao.',
                           'Duy đứng bên bình cuối.', 'Sen đứng dưới cây đa.'):
                found = [p for p in cleaned if p.startswith(prefix)]
                if len(found) != 1:
                    raise ValueError('Farewell edit anchor changed')
                cleaned.remove(found[0])
                changes.append({'action': 'remove_repeated_farewell_or_departure', 'original': found[0]})

        folder = output / f'tap-{number:02d}'
        folder.mkdir(exist_ok=True)
        narration = '\n\n'.join(cleaned) + '\n'
        metadata = {'title': f'Ngôi Làng Không Có Người Chết — Tập {number:02d}: {title.title()}',
                    'slug': f'ngoi-lang-v41-{sha[:12]}-tap-{number:02d}',
                    'series': 'Ngôi Làng Không Có Người Chết', 'language': 'vi',
                    'rights': 'unknown', 'rights_note': 'Production draft from user-supplied document; existing approval is not fabricated.',
                    'voice_cast': CAST,
                    'intake': {'source_path': str(source.resolve()), 'source_sha256': sha, 'series_episode_no': number,
                               'revision': REVISION, 'editorial_status': 'needs_editorial_review', 'target_duration_sec': 660}}
        markdown = '---\n' + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + '---\n\n## Lời đọc\n\n' + narration
        (folder / 'narration.txt').write_text(narration)
        (folder / 'story.md').write_text(markdown)
        parsed = parse_story_file(folder / 'story.md')
        if len(parsed['chapters']) != 1 or parsed['chapters'][0]['content'] != narration.strip():
            raise ValueError('Intake parser changed narration')
        (folder / 'changes.json').write_text(json.dumps(changes, ensure_ascii=False, indent=2))
        diff = ''.join(difflib.unified_diff(originals[number].splitlines(True), narration.splitlines(True),
                                           fromfile=f'tap-{number:02d}-source', tofile=f'tap-{number:02d}-{REVISION}'))
        (folder / 'changes.diff').write_text(diff)
        entry = {'series_episode_no': number, 'title': title, 'directory': str(folder),
                 'narration_sha256': hashlib.sha256(narration.encode()).hexdigest(),
                 'characters': len(narration), 'whitespace_units': len(narration.split()),
                 'changes': len(changes), 'status': 'NEEDS_EDITORIAL_REVIEW', 'review_items': REVIEW[number],
                 'audio_generated': False, 'actual_duration_sec': None, 'published': False}
        (folder / 'production_status.json').write_text(json.dumps(entry, ensure_ascii=False, indent=2))
        entries.append(entry)

    settings = {'series': 'Ngôi Làng Không Có Người Chết', 'revision': REVISION,
                'source_sha256': sha, 'episode_count': 12, 'target_duration_sec': 660,
                'editorial_duration_range_sec': [600, 720],
                'environment_overrides_for_this_series_only': {'STORY_ENGINE__TARGET_DURATION_SEC': '660', 'ALLOW_PAID_API': 'false', 'TTS_PROVIDER': 'vieneu', 'VIENEU_VOICE': 'Thiện Minh', 'PUBLISH_UPLOAD_ENABLED': 'false'},
                'voice_cast': CAST, 'unassigned_roles': ['Cẩn', 'Trạch', 'Nguyệt', 'dân làng'],
                'note': 'No production environment variables changed. Review scripts and assign speakers before TTS.'}
    (output / 'series_config.json').write_text(json.dumps(settings, ensure_ascii=False, indent=2))
    manifest = {'revision': REVISION, 'source': str(source.resolve()), 'source_sha256': sha,
                'api_cost_vnd': 0, 'database_changed': False, 'episodes': entries}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    rows = ['# Tiến độ thực thi V4.1', '', '12 tập đã tách thành bản lời đọc và bản nhập tương thích ứng dụng. Đây là đề xuất biên tập, chưa khóa lời hoặc duyệt bản quyền. Chưa tạo audio/video.', '',
            '| Tập | Tên | Số chỉnh sửa | Trạng thái |', '|---|---|---:|---|']
    rows += [f"| {e['series_episode_no']:02d} | {e['title']} | {e['changes']} | Cần duyệt biên tập |" for e in entries]
    rows += ['', 'Mỗi thư mục có narration.txt, story.md, changes.json, changes.diff, production_status.json.', '',
             'Việc còn lại: giải quyết review_items → khóa lời/cast → TTS local theo từng tập → đo thời lượng thật → mix/visual/QC → nghe duyệt → duyệt xuất bản riêng.', '',
             'Các đoạn bị rút và câu được thêm đều lưu trong changes.json/diff. Không tự sửa cơ chế canon còn mâu thuẫn. Không tạo TTS hàng loạt từ bản chưa khóa để tránh lãng phí take.']
    (output / 'PROGRESS.md').write_text('\n'.join(rows) + '\n')
    if hashlib.sha256(source.read_bytes()).hexdigest() != sha:
        raise ValueError('Source changed during preparation')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    print(prepare(args.source, args.out))
