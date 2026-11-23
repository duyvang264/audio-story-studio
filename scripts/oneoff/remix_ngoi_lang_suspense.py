"""Scene-directed local suspense remix; preserve narration and copy existing video streams."""
from pathlib import Path
import json
import shutil
import hashlib

import numpy as np

from studio import media, qc
from studio.audio_engine import production
from studio.audio_engine.assets import Catalog
from studio.audio_engine.gate import AudioQCError, ListeningReviewRequired
from studio.audio_engine.io import dump, identity, write, digest
from studio.audio_engine.mastering import master
from studio.audio_engine.soundscape import SoundCue
from studio.config import get_settings, PROJECT_ROOT

BASE = PROJECT_ROOT / 'storage/output/8/v41-rebuild-1/34e1897a08152ba5'
REVISION = 'suspense-v2'


def log(stage, **data):
    print(json.dumps({'stage': stage, **data}, ensure_ascii=False), flush=True)


def main():
    original = BASE / 'episode'
    out = BASE.parent / REVISION; d = out / 'episode'; d.mkdir(parents=True, exist_ok=True)
    for name in ('episode_script.json', 'transcript_timeline.json', 'scene_plan.json', 'timeline.json', 'subtitles.srt', 'tiktok.srt'):
        target = d / name
        if not target.exists(): shutil.copy2(original / name, target)
    # Same immutable narration/source evidence; only the mix and downstream evidence change.
    old_state = json.loads((original / 'audio_engine/state.json').read_text())
    state = {k: old_state[k] for k in ('source_audio', 'source_report', 'clean_audio', 'repair_history', 'narration_report')}
    dump(d / 'audio_engine/state.json', state)
    s = get_settings().model_copy(update={'tts_provider': 'vieneu', 'auto_chain': False, 'publish_upload_enabled': False})
    tr = json.loads((d / 'transcript_timeline.json').read_text())
    scenes = json.loads((d / 'scene_plan.json').read_text())['scenes']
    plan = json.loads((original / 'sound_cues.json').read_text())
    total = tr['duration_sec']; events = []
    # Chunk timestamps are measured; phrase anchors select a chunk, not invented word alignment.
    anchors = [(1, 'điều làm tôi sợ', 'Bức ảnh dưới nước'), (3, 'hai đứa trẻ', 'Hai giọng trẻ con'),
               (9, 'tiếng nước', 'Giọng tài xế biến mất'), (12, 'Bà ơi', 'Đứa trẻ trong bản ghi'),
               (16, 'Ngừng thở', 'Nhật ký báo tử'), (17, 'Minh ơi', 'Giọng mẹ dưới giếng'),
               (19, 'chợt biến mất', 'Mất ký ức'), (21, 'Tiếng thứ mười ba', 'Chuông vang trong đầu'),
               (23, 'Đứa nào', 'Hai đứa trẻ cùng gọi bố'), (24, 'Bà ơi, mở cửa', 'Minh ngoài cửa'),
               (26, 'Vậy', 'Bà phải chọn ai')]
    for pn, phrase, label in anchors:
        group = [ch for ch in tr['chunks'] if ch['paragraph_no'] == pn]
        chosen = next((ch for ch in group if phrase.casefold() in ch['text'].casefold()), None)
        if chosen is None: raise ValueError(f'Missing sound anchor {pn}: {phrase}')
        onset = chosen['start']
        previous = [ch for ch in tr['chunks'] if ch['end'] <= onset]
        gap_start = previous[-1]['end'] if previous else 0
        # A short background hush immediately before the selected speech chunk.
        hush_start = max(gap_start, onset - .32)
        events.append({'label': label, 'paragraph_no': pn, 'chunk_no': chosen['chunk_no'],
                       'start_sec': onset, 'hush_start_sec': hush_start, 'hush_duration_sec': onset - hush_start,
                       'timing': 'measured narration chunk boundary'})
    # Make suspense passages audible without raising the entire soundtrack.
    for cue in plan['cues']:
        if cue['stem'] == 'music':
            delta = 4.0 if cue['emotional_intensity'] >= 3 else 1.5
            cue['gain_db'] = min(-5.5, cue['gain_db'] + delta)
        elif cue['stem'] == 'sfx' and cue['asset_id'] == 'wooden_door_knock':
            cue['gain_db'] = -10
    # Original locally composed dissonant pulse motif: no external assets or API.
    rate = s.audio.sample_rate; n = rate * 12; t = np.arange(n) / rate
    rng = np.random.default_rng(20261123)
    motif = np.zeros(n, dtype=np.float64)
    notes = [146.83, 155.56, 146.83, 138.59, 146.83, 207.65, 155.56, 138.59]
    for beat in np.arange(0, 12, .6):
        dt = t - beat; env = np.where(dt >= 0, np.exp(-np.maximum(dt, 0) * 8), 0)
        f = notes[int(round(beat / .6)) % len(notes)]
        motif += env * (.045 * np.sin(2 * np.pi * f * dt) + .018 * np.sin(2 * np.pi * f * 2.005 * dt))
    motif += .018 * np.sin(2 * np.pi * 49 * t) + .012 * np.sin(2 * np.pi * 51.2 * t)
    motif *= np.linspace(.35, 1, n)
    edge = rate // 20; motif[:edge] *= np.linspace(0, 1, edge); motif[-edge:] *= np.linspace(1, 0, edge)
    motif *= min(1, .18 / max(float(np.abs(motif).max()), 1e-9))
    motif_path = out / 'assets/suspense_pulse.wav'
    if not motif_path.exists(): write(motif_path, motif.astype(np.float32), rate)
    assets = json.loads((original / 'sound_assets_resolved.json').read_text())
    assets['original_suspense_pulse'] = {**identity(motif_path), 'asset_id': 'original_suspense_pulse',
        'license': 'CC0-1.0', 'source': 'Original local DSP composition for this episode',
        'attribution': '', 'fallback': False}
    catalog = Catalog(s.storage, s.audio, PROJECT_ROOT)

    def add(event, asset, stem, start, duration, gain, trigger):
        start = max(0, start); duration = min(duration, total - start)
        if duration < .02: return
        scene = next(sc for sc in scenes if sc['start'] <= event['start_sec'] < sc['end'])
        fade_in = min(int(duration * 1000 / 5), 1200 if stem == 'music' else 20)
        fade_out = min(int(duration * 1000 / 4), 700 if stem == 'music' else 80)
        cue = SoundCue(scene_id=scene['scene_no'], cue_id=f'suspense-{len(plan["cues"])+1:04d}',
              asset_id=asset, category=asset, stem=stem, trigger=trigger + ': ' + event['label'],
              relative_position=max(0, min(1, (start - scene['start']) / (scene['end'] - scene['start']))),
              start_ms=round(start * 1000), duration_ms=round(duration * 1000), gain_db=gain,
              fade_in_ms=fade_in, fade_out_ms=fade_out, priority=9,
              emotional_intensity=4.8 if event['paragraph_no'] >= 21 else 4,
              pre_roll_ms=max(0, round((scene['start'] - start) * 1000))).model_dump()
        plan['cues'].append(cue)
        if stem != 'silence' and asset not in assets: assets[asset] = catalog.resolve(asset)

    for event in events:
        at = event['start_sec']; pn = event['paragraph_no']
        add(event, 'original_suspense_pulse', 'music', at - 9, 8.5, -5.5, 'lead_in_tension')
        add(event, 'dark_riser', 'sfx', at - 3.5, 3.1, -8, 'pre_reveal_riser')
        add(event, 'sudden_silence', 'silence', event['hush_start_sec'], event['hush_duration_sec'], -80, 'background_hush')
        add(event, 'eerie_stinger' if pn in (12, 17, 24, 26) else 'low_boom', 'sfx', at, 1.8, -6.5, 'reveal_accent')
        add(event, 'supernatural_reveal' if pn < 21 else 'climax', 'music', at + .2, 8, -7, 'reveal_music')
        if pn in (16, 19, 24): add(event, 'heartbeat', 'sfx', at + 1, 4, -10, 'heartbeat_tension')
    plan['editorial_revision'] = REVISION; plan['emphasis_events'] = events
    dump(d / 'sound_cues.json', plan); dump(d / 'sound_assets_resolved.json', assets)
    dump(out / 'sound_direction.json', {'revision': REVISION, 'events': events,
         'narration_unchanged': identity(Path(state['clean_audio'])),
         'note': 'Background hush only; no ordinary audible thirteenth bell; speech ducking and mastering ceilings retained.'})
    log('mix', events=len(events), cues=len(plan['cues']))
    production.sound_timeline(8, s, d)
    production.mix_episode(8, s, d)
    production.master_episode(8, s, d); production.master_qc(8, s, d)
    state = production._state(d); audio = out / 'audio'; audio.mkdir(exist_ok=True)
    final_audio = audio / 'final_audio.wav'; shutil.copy2(state['master_audio'], final_audio)
    media.ffmpeg(['-i', str(final_audio), '-c:a', 'libmp3lame', '-b:a', '192k', str(audio / 'final_audio_review.mp3')])
    tl = json.loads((d / 'timeline.json').read_text()); tl['audio'].update(production.audio_fields(d))
    dump(d / 'timeline.json', tl)
    log('mux_long')
    video = out / 'final_video.mp4'
    media.ffmpeg(['-i', str(BASE / 'final_video.mp4'), '-i', str(final_audio), '-map', '0:v:0', '-map', '1:a:0',
                  '-map', '0:s?', '-c:v', 'copy', '-c:s', 'copy', '-c:a', 'aac', '-b:a', s.audio.aac_bitrate,
                  '-ar', str(s.audio.sample_rate), '-t', str(tl['duration_sec']), '-movflags', '+faststart', str(video)])
    long_qc = qc.check_video(8, video, expect_height=1080, settings=s, episode_dir=d)
    log('long_qc', passed=long_qc['passed'], info=long_qc['info'])
    meta = json.loads((original / 'tiktok.json').read_text()); short_path = out / 'tiktok_1080x1920.mp4'
    cut = audio / 'short_cut.wav'
    media.ffmpeg(['-ss', str(meta['source_start']), '-i', str(final_audio), '-t', str(meta['duration_sec']), '-c:a', 'pcm_f32le', str(cut)])
    short_master = master(cut, d / 'audio_engine/short_masters', s.audio.model_copy(update={'target_lufs': meta['target_lufs']}))
    meta.update(video=str(short_path), srt=str(d / 'tiktok.srt'), short_master_report=str(Path(short_master['output']['path']).parent / 'qc/audio_qc_report.json'))
    dump(d / 'tiktok.json', meta)
    log('mux_short')
    media.ffmpeg(['-i', str(BASE / 'tiktok_1080x1920.mp4'), '-i', short_master['output']['path'],
                  '-map', '0:v:0', '-map', '1:a:0', '-map', '0:s?', '-c:v', 'copy', '-c:s', 'copy',
                  '-c:a', 'aac', '-b:a', s.audio.aac_bitrate, '-t', str(meta['duration_sec']), '-movflags', '+faststart', str(short_path)])
    short_qc = qc.check_short(8, s, episode_dir=d); gates = {}
    for name, path, is_short in [('long', video, False), ('short', short_path, True)]:
        try: production.assert_publishable(8, s, path, short=is_short, episode_dir=d); gates[name] = 'PASS'
        except ListeningReviewRequired: gates[name] = 'NEEDS_LISTENING_REVIEW'
        except AudioQCError as exc: gates[name] = 'FAIL: ' + str(exc)
    report = {'revision': REVISION, 'output': str(out), 'events': events, 'long_qc': long_qc,
              'short_qc': short_qc, 'publish_gate': gates, 'narration_regenerated': False,
              'api_cost_vnd': 0, 'published': False}
    dump(out / 'run_report.json', report); log('complete', gates=gates, output=str(out))
    if not long_qc['passed'] or not short_qc['passed'] or any(v.startswith('FAIL') for v in gates.values()): raise SystemExit(2)
    if 'NEEDS_LISTENING_REVIEW' in gates.values(): raise SystemExit(3)


if __name__ == '__main__': main()
