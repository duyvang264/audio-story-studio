"""Free local ASR comparison; flags possible errors, never treats ASR as ground truth."""
from pathlib import Path
import json,os,re,difflib,time
ROOT=Path(__file__).resolve().parents[1]
os.environ.update({'HF_HOME':str(ROOT/'storage/models/whisper-cache'),'HF_HUB_DISABLE_XET':'1','HF_HUB_DISABLE_TELEMETRY':'1','XDG_CACHE_HOME':str(ROOT/'storage/tools/vieneu/cache')})
from faster_whisper import WhisperModel
import soundfile as sf
import soxr
p=ROOT/'storage/audio/episodes/8/thien-minh-dien-cam-v1';tr=json.loads((p/'transcript_timeline.json').read_text());saved=p/'asr-transcript.json'
if saved.exists():
 segments=json.loads(saved.read_text())['segments']
else:
 print('Loading free local Whisper small model...',flush=True)
 model=WhisperModel('small',device='cpu',compute_type='int8',cpu_threads=4,download_root=str(ROOT/'storage/models/whisper-small'))
 audio,sample_rate=sf.read(p/'narration.wav',dtype='float32')
 audio=soxr.resample(audio,sample_rate,16000)
 stream,info=model.transcribe(audio,language='vi',beam_size=5,word_timestamps=True,vad_filter=True,condition_on_previous_text=False)
 segments=[];started=time.monotonic()
 for i,seg in enumerate(stream,1):
  segments.append({'start':seg.start,'end':seg.end,'text':seg.text,'avg_logprob':seg.avg_logprob,'words':[{'start':w.start,'end':w.end,'word':w.word,'probability':w.probability} for w in seg.words or []]})
  if i%5==0:print(f'ASR {seg.end:.0f}/{tr["duration_sec"]:.0f}s; elapsed {time.monotonic()-started:.0f}s',flush=True)
 saved.write_text(json.dumps({'model':'faster-whisper-small','language':'vi','segments':segments},ensure_ascii=False,indent=2))
words=[w for s in segments for w in s['words']]
def normalize(s):return re.sub(r'[^\w\s]',' ',s.lower()).split()
findings=[]
for ch in tr['chunks']:
 spoken=' '.join(w['word'].strip() for w in words if ch['start']-0.04<=(w['start']+w['end'])/2<ch['end']+0.04)
 expected=normalize(ch.get('synthesis_text',ch['text']));actual=normalize(spoken)
 matcher=difflib.SequenceMatcher(None,expected,actual,autojunk=False);diff=[]
 for op,i,j,a,b in matcher.get_opcodes():
  if op!='equal':diff.append({'op':op,'expected':' '.join(expected[i:j]),'asr':' '.join(actual[a:b])})
 error=round(1-matcher.ratio(),3)
 findings.append({'chunk_no':ch['chunk_no'],'start':ch['start'],'end':ch['end'],'voice':ch['voice'],'expected':ch.get('synthesis_text',ch['text']),'asr':spoken,'difference_score':error,'diff':diff,'needs_review':bool(diff)})
report={'note':'ASR comparison identifies candidates only; ASR can misrecognize names, dates and quiet speech. Human-confirmed errors must be distinguished from ASR errors.','paid_api':False,'findings':findings}
(p/'pronunciation-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
for f in sorted(findings,key=lambda x:-x['difference_score'])[:25]:print(json.dumps(f,ensure_ascii=False),flush=True)
