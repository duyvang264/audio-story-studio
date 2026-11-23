"""Create an offline, directed multi-voice version of episode 8, preserving earlier masters."""
from pathlib import Path
import hashlib,json,os,re,subprocess,time
ROOT=Path(__file__).resolve().parents[1]
os.environ.update({'HF_HOME':str(ROOT/'storage/models/vieneu'),'XDG_CACHE_HOME':str(ROOT/'storage/tools/vieneu/cache'),'HF_HUB_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1'})
import numpy as np
import soundfile as sf
from vieneu import Vieneu

OUT=ROOT/'storage/audio/episodes/8/thien-minh-dien-cam-v1'
OUT.mkdir(parents=True,exist_ok=True);(OUT/'chunks').mkdir(exist_ok=True)
script=json.loads((ROOT/'storage/episodes/8/episode_script.json').read_text())
CAST={'narrator':'Thiện Minh','minh':'Thiện Minh','grandmother':'Ngọc Linh','sen':'Đoan Trang','mother':'Ngọc Huyền','father':'Thanh Bình','driver':'Thiền Tâm Đức','child':'Trúc Ly','unknown':'Thiền Tâm Đức','double':'Thiện Minh'}
QUOTES={1:['father'],2:['grandmother'],5:['mother','mother'],6:['minh','mother','mother'],8:['driver'],9:['driver'],10:['minh','driver'],12:['grandmother'],13:['grandmother'],14:['child'],15:['grandmother'],17:['minh','grandmother','grandmother'],19:['narrator','narrator'],20:['narrator','narrator','narrator'],21:['sen','sen','sen'],22:['sen'],23:['mother','minh'],24:['grandmother','mother'],26:['narrator'],28:['unknown'],29:['unknown','grandmother'],30:['grandmother'],31:['father','child','child','father'],32:['double','double','double']}
SCENES={1:('Ám ảnh, hồi tưởng; chậm và rõ ở câu mở',0.96),2:('Đau lòng, dò hỏi; giữ nhịp trò chuyện',0.98),3:('Dè chừng, nghi ngờ tăng dần',1.00),4:('Ấm áp khi gặp bà, chùng xuống ở bát cơm',0.96),5:('Hoang mang, hụt hẫng vì ký ức bị mất',0.98),6:('Tập trung điều tra, dừng ở phát hiện trong nhật ký',0.98),7:('Cảnh báo kín đáo, gấp dần khi nghe giếng',1.02),8:('Nguy hiểm sát cửa, tăng nhịp rồi chùng ở mất ký ức',1.08),9:('Dồn theo tiếng chuông, dừng ở tiếng thứ mười ba',1.03),10:('Day dứt chuyển sang hoảng sợ; chậm câu kết',1.05)}
units=[]
for sec in script['sections']:
 sn=sec['section_no'];direction,base_speed=SCENES[sn]
 for pi,p in enumerate(sec['paragraphs']):
  pn=p['paragraph_no'];text=p['text'];parts=re.split(r'("[^"]+")',text);qi=0;local=[]
  for part in parts:
   if not part.strip():continue
   quoted=part.startswith('"')
   if quoted:
    role=QUOTES[pn][qi];qi+=1;spoken=part[1:-1]
   else:role='narrator';spoken=part.strip()
   if not re.search(r'\w',spoken):continue
   speed=base_speed
   if role in ('grandmother','father'):speed=min(speed,0.96)
   if role=='sen':speed=1.03
   if role=='driver':speed=0.97
   if role=='mother':speed=0.99 if sn<7 else 1.02
   if role=='child':speed=1.02
   if pn==25:speed=0.97
   if role=='double':speed=0.96
   if pn==32 and qi==3 and quoted:speed=0.93
   render_text=spoken
   render_text=render_text.replace('13/08/2008. Ngừng thở lúc 23:47.', 'Ngày mười ba tháng tám năm hai nghìn không trăm linh tám. Ngừng thở lúc hai mươi ba giờ bốn mươi bảy phút.')
   local.append({'section_no':sn,'paragraph_no':pn,'text':spoken,'synthesis_text':render_text,'source_fragment':part,'role':role,'voice':CAST[role],'speed':speed,'direction':direction,'pause_after':0.16 if role=='narrator' else 0.26})
  assert qi==len(QUOTES.get(pn,[])),f'Quote attribution mismatch paragraph {pn}'
  # Verify all source words remain in the same order; quotes are performance punctuation.
  normalize=lambda x:re.sub(r'\s+',' ',x.replace('"','')).strip()
  assert normalize(' '.join(u['text'] for u in local))==normalize(text)
  local[-1]['pause_after']=0.55 if pi<len(sec['paragraphs'])-1 else 1.15
  if pn in (1,14,20,23,25,29,32):local[-1]['pause_after']=0.85 if pn!=32 else 1.6
  units.extend(local)
for i,u in enumerate(units,1):u['chunk_no']=i
(OUT/'performance_plan.json').write_text(json.dumps({'voice_cast':CAST,'scenes':SCENES,'units':units,'paid_api':False,'note':'Scene direction is an editorial target. Delivery uses preset voice prosody, dialogue casting, pitch-preserving tempo and explicit pauses; no unsupported emotion tags.'},ensure_ascii=False,indent=2))
print(json.dumps({'stage':'loading_model','units':len(units),'scenes':10,'paid_api':False}),flush=True)
tts=Vieneu(mode='v3turbo',backend='onnx',device='cpu',precision='fp32',threads=4)
sr=48000;cursor=int(sr*0.6);chunks=[];started=time.monotonic();raw=OUT/'narration_raw.wav'
with sf.SoundFile(raw,'w',samplerate=sr,channels=1,subtype='PCM_16') as master:
 master.write(np.zeros(cursor,dtype=np.float32))
 for i,u in enumerate(units,1):
  key=hashlib.sha256(json.dumps([u['voice'],u['synthesis_text'],u['speed'],'v1'],ensure_ascii=False).encode()).hexdigest()[:20]
  wav=OUT/'chunks'/f'{key}.wav';ready=OUT/'chunks'/f'{key}-paced.wav'
  if not wav.exists():
   audio=tts.infer(u['synthesis_text'],voice=u['voice'],temperature=0.8,max_chars=256)
   assert np.isfinite(audio).all() and len(audio)>sr*0.12,f'Empty audio chunk {i}'
   tts.save(audio,str(wav))
  if not ready.exists():
   subprocess.run(['ffmpeg','-hide_banner','-nostdin','-y','-loglevel','error','-i',str(wav),'-af',f'atempo={u["speed"]},afade=t=in:d=0.008,loudnorm=I=-18:TP=-2:LRA=11','-ar',str(sr),'-ac','1','-c:a','pcm_s16le',str(ready)],check=True)
  audio,_=sf.read(ready,dtype='float32');start=cursor/sr;master.write(audio);cursor+=len(audio)
  chunks.append({**u,'audio_path':str(ready),'start':round(start,3),'end':round(cursor/sr,3),'duration_sec':round(len(audio)/sr,3)})
  pause=int(sr*u['pause_after']);master.write(np.zeros(pause,dtype=np.float32));cursor+=pause
  if i%10==0 or i==len(units):print(f'Voice segments {i}/{len(units)}; elapsed {time.monotonic()-started:.0f}s',flush=True)
master=OUT/'narration.wav';mp3=OUT/'ngoi-lang-tap-01-thien-minh-da-giong.mp3'
subprocess.run(['ffmpeg','-hide_banner','-nostdin','-y','-loglevel','error','-i',str(raw),'-af','loudnorm=I=-16:TP=-1.5:LRA=11','-ar',str(sr),'-ac','1','-c:a','pcm_s16le',str(master)],check=True)
subprocess.run(['ffmpeg','-hide_banner','-nostdin','-y','-loglevel','error','-i',str(master),'-c:a','libmp3lame','-b:a','160k','-metadata','title=Ngôi Làng Không Có Người Chết — Tập 1 | Thiện Minh và các nhân vật',str(mp3)],check=True)
paragraphs={}
for ch in chunks:
 p=paragraphs.setdefault(ch['paragraph_no'],{'paragraph_no':ch['paragraph_no'],'section_no':ch['section_no'],'start':ch['start']});p['end']=ch['end'];p['pause_after']=ch['pause_after']
timeline={'episode_id':8,'duration_sec':round(cursor/sr,3),'lead_in_sec':0.6,'audio_path':str(master),'tts':'vieneu|3.8.5|multi-voice|thien-minh-v1','timing_method':'measured_per_performance_segment','chunks':chunks,'paragraphs':list(paragraphs.values())}
(OUT/'transcript_timeline.json').write_text(json.dumps(timeline,ensure_ascii=False,indent=2))
(OUT/'report.json').write_text(json.dumps({'episode_id':8,'audio':str(mp3),'wav':str(master),'duration_sec':timeline['duration_sec'],'units':len(units),'sections':10,'paid_api':False,'api_cost':0,'generation_seconds':round(time.monotonic()-started,1),'listening_review':'pending'},ensure_ascii=False,indent=2))
print(json.dumps({'audio':str(mp3),'duration_sec':timeline['duration_sec'],'units':len(units)},ensure_ascii=False),flush=True)
