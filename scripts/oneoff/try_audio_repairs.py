"""Offline repair auditions; ASR comparisons are advisory, not listening approval."""
from pathlib import Path
import os,json,subprocess,time
ROOT=Path(__file__).resolve().parents[2]
os.environ.update({'HF_HOME':str(ROOT/'storage/models/vieneu'),'XDG_CACHE_HOME':str(ROOT/'storage/tools/vieneu/cache'),'HF_HUB_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1'})
import numpy as np
import soundfile as sf
from vieneu import Vieneu
p=ROOT/'storage/audio/episodes/8/thien-minh-dien-cam-v1';out=p.parent/'thien-minh-sua-am-v2';out.mkdir(exist_ok=True)
t=json.loads((p/'transcript_timeline.json').read_text());ids=[2,14,26,31,92,99];by_id={ch['chunk_no']:ch for ch in t['chunks']}
tts=Vieneu(mode='v3turbo',backend='onnx',device='cpu',precision='fp32',threads=4)
repairs=[];sr=48000
for no in ids:
 c=by_id[no];text=c.get('synthesis_text',c['text']).rstrip(':')
 if no==92:text=text.replace('MINH2008LASTBREATH','Minh, năm hai nghìn không trăm linh tám, hơi thở cuối cùng')
 if not text.endswith(('.', '?','!','…')):text+='.'
 raw=out/f'repair-{no:03d}-source.wav';wav=out/f'repair-{no:03d}.wav'
 if not raw.exists():
  a=tts.infer(text,voice=c['voice'],temperature=0.6,max_chars=180)
  assert np.isfinite(a).all() and len(a)>sr*.15
  sf.write(raw,a,sr,subtype='PCM_24')
 subprocess.run(['ffmpeg','-hide_banner','-nostdin','-y','-loglevel','error','-i',str(raw),'-af','highpass=f=60,lowpass=f=10500,afftdn=nr=3:nf=-55:tn=1,loudnorm=I=-18:TP=-2:LRA=11','-ar',str(sr),'-c:a','pcm_s24le',str(wav)],check=True)
 repairs.append({'chunk_no':no,'old_start':c['start'],'voice':c['voice'],'text':text,'source':str(raw),'audio':str(wav),'old_audio':c['audio_path']})
 print('Created repair candidate',no,flush=True)
combined=out/'cac-cau-doc-lai.wav'
with sf.SoundFile(combined,'w',samplerate=sr,channels=1,subtype='PCM_24') as f:
 for r in repairs:
  a,_=sf.read(r['audio'],dtype='float32');f.write(a);f.write(np.zeros(sr,dtype='float32'))
subprocess.run(['ffmpeg','-hide_banner','-nostdin','-y','-loglevel','error','-i',str(combined),'-c:a','libmp3lame','-b:a','192k',str(out/'cac-cau-doc-lai.mp3')],check=True)
(out/'repair-candidates.json').write_text(json.dumps({'paid_api':False,'human_listening_review':'pending','repairs':repairs},ensure_ascii=False,indent=2))
print('Created repair auditions; not yet adopted into production.',flush=True)
