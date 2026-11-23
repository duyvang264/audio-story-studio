"""Offline V4.1 episode-one rebuild; no database writes, generation fees or publishing."""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

if '--worker' not in sys.argv:
    from studio import media, qc, render, shorts, timeline
    from studio.audio_engine import production
    from studio.audio_engine.gate import AudioQCError, ListeningReviewRequired
    from studio.audio_engine.io import decode, dump, write, digest
    from studio.config import get_settings
    from studio.story import split_sentences
    from studio.tts.text import spoken_text

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'storage/production/ngoi-lang-khong-co-nguoi-chet/v4.1-production-draft-1-34a2134fa1fa/tap-01/narration.txt'
VERSION = 'v41-rebuild-1'
CAST = {'narrator': 'Thiện Minh', 'minh': 'Thiện Minh', 'grandmother': 'Ngọc Linh',
        'sen': 'Đoan Trang', 'mother': 'Ngọc Huyền', 'father': 'Thanh Bình',
        'driver': 'Thiền Tâm Đức', 'child': 'Trúc Ly', 'double': 'Thiện Minh'}
QUOTES = {3: ['father'], 4: ['grandmother'], 5: ['mother', 'mother'],
          6: ['minh', 'mother', 'mother'], 8: ['driver'], 10: ['grandmother'],
          11: ['grandmother'], 12: ['child'], 13: ['minh', 'grandmother', 'grandmother'],
          15: ['narrator', 'narrator'], 16: ['narrator', 'narrator', 'narrator'],
          17: ['mother', 'minh'], 18: ['grandmother', 'mother'], 20: ['narrator'],
          22: ['grandmother'], 23: ['father', 'child', 'child', 'father'],
          24: ['double', 'double', 'double'], 26: ['double', 'double', 'grandmother']}
IMAGES = {1: ['letter', 'well'], 2: ['letter'], 3: ['recording', 'jars'], 4: ['letter'],
          5: ['phone'], 6: ['phone'], 7: ['village'], 8: ['bridge'], 9: ['village'],
          10: ['grandmother'], 11: ['house'], 12: ['house'], 13: ['grandmother'],
          14: ['recording'], 15: ['recording'], 16: ['recording', 'jars'],
          17: ['sen', 'well'], 18: ['phone', 'grandmother'], 19: ['phone'],
          20: ['grandmother'], 21: ['recording', 'well'], 22: ['redjar'],
          23: ['jars', 'recording'], 24: ['redjar', 'door'], 25: ['house'], 26: ['door', 'grandmother']}


def log(stage, **values):
    print(json.dumps({'stage': stage, **values}, ensure_ascii=False), flush=True)


def worker(request_path: Path):
    # Run in the existing optional Python 3.12 VieNeu environment.
    import os
    os.environ.update(HF_HOME=str(ROOT / 'storage/models/vieneu'), HF_HUB_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', XDG_CACHE_HOME=str(ROOT / 'storage/tools/vieneu/cache'))
    import soundfile as sf
    from vieneu import Vieneu
    request = json.loads(request_path.read_text())
    tts = Vieneu(mode='v3turbo', backend='onnx', device='cpu', precision='fp32', threads=4)
    for index, item in enumerate(request['items'], 1):
        path = Path(item['path'])
        if path.exists():
            continue
        a = tts.infer(item['synthesis_text'], voice=item['voice'], temperature=.65, max_chars=220)
        if not np.isfinite(a).all() or len(a) < 48000 * .15:
            raise ValueError(f'Invalid TTS chunk {item["chunk_no"]}')
        native = path.with_suffix('.source.wav')
        if not native.exists():
            sf.write(native, a, 48000, subtype='FLOAT')
        peak = float(np.max(np.abs(a)))
        gain = min(1.0, .88 / max(peak, 1e-9))
        temp = path.with_suffix('.part.wav')
        # Preserve native source; tempo is mild, short fades protect splices.
        subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-y', '-loglevel', 'error',
                        '-i', str(native), '-af', f'volume={gain},atempo={item["speed"]},afade=t=in:d=0.004',
                        '-ar', '48000', '-ac', '1', '-c:a', 'pcm_f32le', str(temp)], check=True)
        temp.replace(path)
        log('tts_chunk', completed=index, total=len(request['items']), chunk_no=item['chunk_no'], voice=item['voice'])


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        worker(Path(sys.argv[2])); return
    text = SOURCE.read_text()
    revision = hashlib.sha256((text + VERSION).encode()).hexdigest()[:16]
    out = ROOT / 'storage/output/8' / VERSION / revision
    d = out / 'episode'; audio = out / 'audio'; cache = audio / 'chunks'
    for p in (out, d, cache): p.mkdir(parents=True, exist_ok=True)
    s = get_settings()
    s = s.model_copy(update={'tts_provider': 'vieneu', 'publish_upload_enabled': False,
                            'auto_chain': False, 'story_engine': s.story_engine.model_copy(update={'target_duration_sec': 660})})
    paras = text.strip().split('\n\n')
    if len(paras) != 26: raise ValueError('Source changed; paragraph casting must be reviewed')
    script = {'episode_id': 8, 'story_id': 2, 'story_slug': 'ngoi-lang-v41-tap01',
              'series': 'Ngôi Làng Không Có Người Chết', 'title': 'Lá Thư Của Người Đã Chết — V4.1',
              'story_title': 'Ngôi Làng Không Có Người Chết', 'style_profile': 'dark_fantasy_cinematic',
              'characters': [], 'locations': [], 'voice_cast': CAST, 'sections': [],
              'production_revision': VERSION, 'source_narration_sha256': digest(SOURCE)}
    units = []
    for pn, paragraph in enumerate(paras, 1):
        script['sections'].append({'section_no': pn, 'chapter_no': 1, 'chapter_title': f'Cảnh {pn:02d}',
                                   'paragraphs': [{'paragraph_no': pn, 'text': paragraph, 'sentences': split_sentences(paragraph)}]})
        qi = 0; local = []
        for part in re.split(r'(“[^”]+”)', paragraph):
            if not part.strip(): continue
            role = 'narrator'
            if part.startswith('“'):
                role = QUOTES[pn][qi]; qi += 1
                part = part[1:-1]
            if not re.search(r'\w', part): continue
            phrases = []
            for sentence in split_sentences(part.strip()):
                if phrases and len(phrases[-1]) + len(sentence) + 1 <= 220:
                    phrases[-1] += ' ' + sentence
                else:
                    phrases.append(sentence)
            for sentence in phrases:
                spoken, issues = spoken_text(sentence, {'MINH_2008_LAST_BREATH': 'Minh, năm hai nghìn không trăm linh tám, hơi thở cuối cùng', 'chữ Y': 'chữ i dài'})
                speed = .98 if pn in (1, 10, 12, 13, 19, 22, 26) else 1.035 if pn in (17, 18, 21, 24) else 1.0
                if role in ('grandmother', 'father', 'double'): speed = min(speed, .98)
                item = {'section_no': pn, 'paragraph_no': pn, 'text': sentence, 'synthesis_text': spoken,
                        'pronunciation_review': issues, 'role': role, 'voice': CAST[role], 'speed': speed,
                        'pause_after': .22 if role == 'narrator' else .28}
                item['chunk_no'] = len(units) + len(local) + 1
                key = hashlib.sha256(json.dumps([VERSION, spoken, role, speed], ensure_ascii=False).encode()).hexdigest()[:20]
                item['path'] = str(cache / f'{key}.wav')
                local.append(item)
        if qi != len(QUOTES.get(pn, [])): raise ValueError(f'Quote casting mismatch paragraph {pn}')
        normalize = lambda v: re.sub(r'\s+', ' ', v.replace('“', '').replace('”', '')).strip()
        if normalize(' '.join(u['text'] for u in local)) != normalize(paragraph):
            raise ValueError(f'Source word order mismatch paragraph {pn}')
        local[-1]['pause_after'] = .65 if pn not in (1, 3, 12, 19, 21, 24, 26) else .95
        units.extend(local)
    dump(d / 'episode_script.json', script)
    dump(out / 'performance_plan.json', {'voice_cast': CAST, 'units': units, 'paid_api': False,
         'source_words_verified': True, 'note': 'Preset voice plus mild tempo/pauses; emotional acting and pronunciation still require listening.'})
    request = out / 'tts_request.json'; dump(request, {'items': units})
    log('start', output=str(out), chunks=len(units), source_sha256=digest(SOURCE))
    python = s.project_path(s.vieneu_python)
    subprocess.run([str(python), str(Path(__file__).resolve()), '--worker', str(request)], check=True)
    log('tts_complete')
    arrays = [np.zeros((int(.4 * 48000), 1), dtype=np.float32)]; cursor = len(arrays[0]); chunks = []
    for item in units:
        a, rate = decode(item['path'], 48000, 1)
        # A short fade-out only at each known splice, never broad noise removal.
        fade = min(192, len(a) // 4)
        if fade: a[-fade:] *= np.linspace(1, 0, fade)[:, None]
        start = cursor / rate; arrays.append(a); cursor += len(a)
        chunks.append({**item, 'audio_path': item['path'], 'start': start, 'end': cursor / rate,
                       'duration_sec': len(a) / rate})
        pause = np.zeros((round(item['pause_after'] * rate), 1), np.float32)
        arrays.append(pause); cursor += len(pause)
    narration = audio / 'narration_raw.wav'
    write(narration, np.concatenate(arrays), 48000, codec='pcm_f32le')
    tr = {'episode_id': 8, 'duration_sec': cursor / 48000, 'audio_path': str(narration),
          'tts': 'vieneu|3.8.5|offline|v41-directed', 'lead_in_sec': .4, 'chunks': chunks,
          'timing_method': 'measured_per_performance_segment'}
    dump(d / 'transcript_timeline.json', tr)
    log('narration_assembled', duration_sec=tr['duration_sec'])
    # Derive all shot cuts from the new spoken boundaries; reuse illustrations only.
    fps = s.fps; total_frames = round(tr['duration_sec'] * fps); scenes = []; motions = ['slow_zoom_in', 'pan_left_to_right', 'slow_zoom_out', 'pan_right_to_left']
    for pn in range(1, 27):
        group = [c for c in chunks if c['paragraph_no'] == pn]
        next_group = [c for c in chunks if c['paragraph_no'] == pn + 1]
        start = scenes[-1]['end'] if scenes else 0
        end = round((next_group[0]['start'] if next_group else total_frames / fps) * fps) / fps
        names = IMAGES[pn]
        # Split long passages further, changing image/motion at ~25–35 sec.
        count = max(len(names), math.ceil((end - start) / (24 if pn in (1, 17, 18, 21, 24, 26) else 35)))
        cuts = [start]
        for k in range(1, count):
            target = start + (end - start) * k / count
            candidates = [c['start'] for c in group if cuts[-1] + 5 < c['start'] < end - (count - k) * 5]
            cut = min(candidates, key=lambda v: abs(v - target)) if candidates else target
            cuts.append(round(cut * fps) / fps)
        cuts.append(end)
        for k, (a, b) in enumerate(zip(cuts, cuts[1:])):
            no = len(scenes) + 1; name = names[k % len(names)]
            image = ROOT / 'storage/episodes/8/generated' / (name + '.png')
            if not image.exists(): raise FileNotFoundError(image)
            intensity = 5 if pn in (24, 26) else 4 if pn >= 17 else 2 if pn in (10, 11, 12) else 3
            scenes.append({'scene_no': no, 'section_no': pn, 'start': a, 'end': b, 'duration': b - a,
                           'frames': round(b * fps) - round(a * fps), 'image': str(image),
                           'asset_name': name, 'motion': motions[(no - 1) % 4], 'version': 1,
                           'summary': paras[pn - 1][:180], 'intensity': intensity, 'mood': 'mysterious', 'characters': []})
    if sum(c['frames'] for c in scenes) != total_frames: raise ValueError('Scene coverage mismatch')
    dump(d / 'scene_plan.json', {'episode_id': 8, 'duration_sec': tr['duration_sec'], 'scenes': scenes})
    cues = timeline.build_cues(chunks); timeline.write_srt(cues, d / 'subtitles.srt')
    dump(d / 'timeline.json', {'episode_id': 8, 'fps': fps, 'total_frames': total_frames,
         'duration_sec': total_frames / fps, 'transition': {'type': 'fade', 'duration_sec': .4},
         'audio': {'narration': str(narration), 'bgm': None},
         'subtitles': {'srt': str(d / 'subtitles.srt'), 'cues': len(cues), 'mode': 'both'}, 'scenes': scenes})
    log('audio_engine', shots=len(scenes), images=len({c['image'] for c in scenes}))
    stages = production.prepare_episode(8, s, d)
    state = production._state(d)
    final_audio = audio / 'final_audio.wav'
    if not final_audio.exists():
        import shutil
        shutil.copy2(state['master_audio'], final_audio)
    media.ffmpeg(['-i', str(final_audio), '-c:a', 'libmp3lame', '-b:a', '192k', str(audio / 'final_audio_review.mp3')])
    log('render_long')
    video = out / 'final_video.mp4'
    if not video.exists(): render.render_episode(8, height=1080, out_path=video, crf=22, settings=s, episode_dir=d)
    long_qc = qc.check_video(8, video, expect_height=1080, settings=s, episode_dir=d)
    log('long_qc', passed=long_qc['passed'], info=long_qc['info'])
    log('render_short')
    short_path = out / 'tiktok_1080x1920.mp4'
    short = shorts.render_short(8, s, episode_dir=d, out_path=short_path)
    short_qc = qc.check_short(8, s, episode_dir=d)
    gates = {}
    for name, path, is_short in [('long', video, False), ('short', short_path, True)]:
        try: production.assert_publishable(8, s, path, short=is_short, episode_dir=d); gates[name] = 'PASS'
        except ListeningReviewRequired: gates[name] = 'NEEDS_LISTENING_REVIEW'
        except AudioQCError as exc: gates[name] = 'FAIL: ' + str(exc)
    result = {'revision': VERSION, 'source': str(SOURCE), 'source_sha256': digest(SOURCE),
              'output': str(out), 'audio': str(final_audio), 'video': str(video), 'short': short,
              'duration_sec': tr['duration_sec'], 'shots': len(scenes), 'stages': stages,
              'long_qc': long_qc, 'short_qc': short_qc, 'publish_gate': gates,
              'api_cost_vnd': 0, 'database_changed': False, 'published': False}
    dump(out / 'run_report.json', result); log('complete', output=str(out), gates=gates)
    if not long_qc['passed'] or not short_qc['passed'] or any(v.startswith('FAIL') for v in gates.values()): raise SystemExit(2)
    if any(v == 'NEEDS_LISTENING_REVIEW' for v in gates.values()): raise SystemExit(3)


if __name__ == '__main__':
    main()
