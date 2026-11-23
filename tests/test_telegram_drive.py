"""Telegram Drive intake and story-bible narration filtering."""
import json

import pytest

from studio import commands, drive, jobs, pipeline, story, worker
from studio.db import connect

BIBLE = '''---
title: Series_Bible
slug: drive-test-bible
rights: original
---

LÀNG THỬ NGHIỆM

SERIES BIBLE

## I. LỜI GIẢI BÍ MẬT

SPOILER_NOT_FOR_NARRATION

## VI. KỊCH BẢN TẬP 01 — V2

EDITORIAL_INTRO_NOT_FOR_NARRATION

## Cảnh 01 — Lá thư

Tôi nhận lá thư của bà. Tôi bước vào làng giữa đêm mưa.

## Cảnh 02 — Giếng

Tôi nghe tiếng gọi dưới giếng. Tôi không đáp lại tiếng gọi đó.

## VII. GHI CHÚ BIÊN TẬP

EDITORIAL_OUTRO_NOT_FOR_NARRATION
'''
FID = 'test_document_123456'


@pytest.fixture
def bible(tmp_path):
    src = tmp_path / 'bible.md'
    src.write_text(BIBLE)
    return src


def test_bible_selects_only_episode_scenes(bible):
    parsed = story.parse_story_file(bible)
    assert parsed['meta']['title'] == 'LÀNG THỬ NGHIỆM — Tập 01'
    assert parsed['meta']['slug'] == 'drive-test-bible-tap-01'
    assert len(parsed['chapters']) == 2
    assert parsed['meta']['intake']['kind'] == 'story_bible'
    narration = '\n'.join(ch['content'] for ch in parsed['chapters'])
    assert 'Tôi nhận lá thư' in narration
    assert 'NOT_FOR_NARRATION' not in narration


def test_unrecognized_bible_requires_manual_review(tmp_path):
    p = tmp_path / 'bible.md'
    p.write_text('SERIES BIBLE\n\n## Ghi chú\n\nBí mật toàn mùa.')
    with pytest.raises(story.StoryError, match='lời kể'):
        story.parse_story_file(p)


def test_script_header_without_scenes_is_rejected(bible):
    bible.write_text(BIBLE.replace('Cảnh 01', 'Phần 01').replace('Cảnh 02', 'Phần 02'))
    with pytest.raises(story.StoryError, match='Cảnh 01'):
        story.parse_story_file(bible)


def test_multi_episode_bible_requires_split(bible):
    bible.write_text(BIBLE + '\n\n## KỊCH BẢN TẬP 02\n\nTập tiếp theo.')
    with pytest.raises(story.StoryError, match='nhiều kịch bản'):
        story.parse_story_file(bible)


@pytest.mark.db
def test_drive_command_to_worker_to_review_and_rights_gate(db, settings, bible, monkeypatch):
    monkeypatch.setattr(drive, 'fetch_story', lambda source, s, client: bible)
    reply, _ = commands.handle_command('/drive https://docs.google.com/document/d/' + FID + '/edit', 111, 111, settings)
    assert 'Đã nhận link' in reply
    again = commands.queue_drive_import(FID, 111, 111)
    assert 'Đang xử lý' in again
    with connect() as c:
        row = c.execute("SELECT * FROM jobs WHERE type='drive_import' ORDER BY id DESC LIMIT 1").fetchone()
        c.execute('UPDATE jobs SET priority=0 WHERE id=%s', (row['id'],))
    assert worker.run_one(settings)
    with connect() as c:
        done = c.execute('SELECT * FROM jobs WHERE id=%s', (row['id'],)).fetchone()
        assert done['status'] == 'succeeded', done['last_error']
        sid = done['result']['story_id']
        assert c.execute('SELECT count(*) AS n FROM episodes WHERE story_id=%s', (sid,)).fetchone()['n'] == 0
    detail = commands.story_text(sid)
    assert '2 mục' in detail and 'Đã tách' in detail
    assert 'SPOILER' not in detail and 'EDITORIAL' not in detail
    assert f'/approve rights {sid}' in detail
    assert f'/story {sid}' in commands.stories_text(111)
    with pytest.raises(story.StoryError, match='rights not approved'):
        story.create_episodes(sid)
    commands.handle_command(f'/approve rights {sid}', 111, 111, settings)
    eps = story.create_episodes(sid)
    script_path = story.build_episode_script(eps[0]['id'])['path']
    script = json.loads(__import__('pathlib').Path(script_path).read_text())
    spoken = ' '.join(p['text'] for sec in script['sections'] for p in sec['paragraphs'])
    assert 'Tôi nhận lá thư' in spoken and 'NOT_FOR_NARRATION' not in spoken
    imported_again = pipeline.h_drive_import(row, settings)
    assert imported_again['story_id'] == sid and not imported_again['created']
    assert jobs.next_stage('drive_import') is None


@pytest.mark.db
def test_bare_drive_link_does_not_enter_pasted_draft(db, settings, monkeypatch):
    called = []
    monkeypatch.setattr(commands, 'queue_drive_import', lambda source, uid, chat: called.append(source) or 'queued')
    reply = commands.handle_update({'message': {'from': {'id': 111}, 'chat': {'id': 111},
                                              'text': 'https://docs.google.com/document/d/' + FID + '/edit'}}, settings)
    assert reply['reply'] == 'queued' and len(called) == 1
    blocked = commands.handle_update({'message': {'from': {'id': 222}, 'chat': {'id': 222},
                                                'text': '/drive ' + FID}}, settings)
    assert blocked['reason'] == 'unauthorized' and len(called) == 1


@pytest.mark.db
def test_drive_terminal_failure_is_visible_without_episode(db, settings, monkeypatch):
    def fail(*args):
        raise drive.DriveError('Google HTTP 403. Không đủ quyền.')
    monkeypatch.setattr(drive, 'fetch_story', fail)
    row = jobs.enqueue('drive_import', None, payload={'file_id': 'denied_document_12345', 'user_id': 111, 'chat_id': 111},
                       idempotency_key='test:denied-drive', priority=-1)
    assert worker.run_one(settings)
    with connect() as c:
        failed = c.execute('SELECT status, attempts FROM jobs WHERE id=%s', (row['id'],)).fetchone()
    assert failed == {'status': 'failed', 'attempts': 1}
    assert 'Không đủ quyền' in commands.drive_jobs_text(111)
    outbox = (settings.storage / 'logs/telegram_outbox.jsonl').read_text()
    assert 'Không nhập được tài liệu Drive' in outbox


def test_telegram_network_error_does_not_echo_bot_token(settings, monkeypatch):
    import httpx
    from studio.telegram import TelegramClient, TelegramError
    from studio.config import Settings
    cfg = Settings(_env_file=None, telegram_bot_token="private-token", telegram_mode="polling", telegram_dry_run=False)
    def fail(*args, **kwargs):
        raise httpx.ConnectError("https://api.telegram.org/botprivate-token/sendMessage")
    monkeypatch.setattr(httpx, "post", fail)
    monkeypatch.setattr("studio.telegram.time.sleep", lambda n: None)
    with pytest.raises(TelegramError) as exc:
        TelegramClient(cfg).send_message(111, "test")
    assert "private-token" not in str(exc.value)
