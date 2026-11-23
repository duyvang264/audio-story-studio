"""Generate two local, free VieNeu voice auditions without changing episode audio."""
from pathlib import Path
import json
import os
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
os.environ['HF_HOME'] = str(ROOT / 'storage/models/vieneu')
os.environ['XDG_CACHE_HOME'] = str(ROOT / 'storage/tools/vieneu/cache')
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.environ['HF_HUB_DISABLE_XET'] = '1'

import numpy as np
import soundfile as sf
from vieneu import Vieneu

outdir = ROOT / 'storage/audio/previews/truyen-2/vieneu-free'
outdir.mkdir(parents=True, exist_ok=True)
script = json.loads((ROOT / 'storage/episodes/8/episode_script.json').read_text())
paragraphs = script['sections'][0]['paragraphs'][:2]
text = '\n\n'.join(p['text'] for p in paragraphs)
(outdir / 'sample-text.txt').write_text(text, encoding='utf-8')
print(json.dumps({'stage': 'loading_local_model', 'characters': len(text), 'paid_api': False}), flush=True)
tts = Vieneu(mode='v3turbo', backend='onnx', device='cpu', precision='fp32', threads=4)
voices = [('Thiện Minh', '01-thien-minh-bac'), ('Thái Sơn', '02-thai-son-nam')]
report = {'provider': 'VieNeu-TTS v3 Turbo', 'version': '3.8.5', 'backend': 'onnx-cpu-fp32', 'paid_api': False, 'api_cost': 0, 'samples': []}
for voice, filename in voices:
    started = time.monotonic()
    wav = outdir / f'{filename}.wav'
    mp3 = outdir / f'{filename}.mp3'
    if not wav.exists():
        print(f'Generating {voice}...', flush=True)
        audio = tts.infer(text, voice=voice, temperature=0.8, max_chars=256)
        assert len(audio) > 48000 and np.isfinite(audio).all(), 'Invalid generated audio'
        tts.save(audio, str(wav))
    subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-y', '-loglevel', 'error', '-i', str(wav), '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11', '-c:a', 'libmp3lame', '-b:a', '160k', str(mp3)], check=True)
    info = sf.info(wav)
    sample = {'voice': voice, 'mp3': str(mp3), 'wav': str(wav), 'duration_sec': round(info.duration, 2), 'sample_rate': info.samplerate, 'generation_seconds': round(time.monotonic() - started, 1)}
    report['samples'].append(sample)
    (outdir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(sample, ensure_ascii=False), flush=True)
print('DONE: two free local voice auditions.', flush=True)
