"""Free, offline character voice auditions for episode 8; preserves production audio."""
from pathlib import Path
import json
import os
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
os.environ.update({'HF_HOME': str(ROOT/'storage/models/vieneu'), 'XDG_CACHE_HOME': str(ROOT/'storage/tools/vieneu/cache'), 'HF_HUB_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1'})
from vieneu import Vieneu
import soundfile as sf
import numpy as np

samples = [
 {'id':'01-ba-tu-ngoc-linh','character':'Bà Tư Lành — phương án A','voice':'Ngọc Linh','direction':'Thân thương, có điều che giấu; kiểm tra giọng có đủ độ tuổi cho bà không.','lines':['Minh à? Sao còn đứng ngoài ấy?', 'Có chứ,', 'Nhưng… bà không nhớ tên nó nữa.', 'Đến lúc con biết chuyện ở giếng rồi.', 'Bà đã mất con một lần rồi.']},
 {'id':'02-ba-tu-quynh-anh','character':'Bà Tư Lành — phương án B','voice':'Quỳnh Anh','direction':'Đối chiếu với phương án A về độ ấm và cảm xúc.','lines':['Minh à? Sao còn đứng ngoài ấy?', 'Có chứ,', 'Nhưng… bà không nhớ tên nó nữa.', 'Đến lúc con biết chuyện ở giếng rồi.', 'Bà đã mất con một lần rồi.']},
 {'id':'03-sen-doan-trang','character':'Sen','voice':'Đoan Trang','direction':'Trẻ, gần gũi; lời cảnh báo kín đáo.','lines':['Đừng gọi tên tôi lớn như thế.', 'Anh còn nhớ tôi à?', 'Vậy thì đừng để họ biết. Những người đang chờ anh quên.', 'Đừng để nó đếm được người cuối cùng.']},
 {'id':'04-me-minh-ngoc-huyen','character':'Mẹ Minh','voice':'Ngọc Huyền','direction':'Lo lắng, cố kiềm chế khi nhắc lại quá khứ.','lines':['Mẹ đã nhìn thấy quan tài.', 'Năm ấy người ta đưa con về lúc gần sáng. Quần áo con ướt sũng. Con không nói câu nào trong ba ngày. Đến đêm thứ tư, con tỉnh lại và hỏi tại sao bố không đưa đứa kia về cùng.', 'Nhà mình chỉ có một mình con.', 'Nếu ai gọi tên con sau nửa đêm, đừng đáp.']},
 {'id':'05-bo-minh-thanh-binh','character':'Bố Minh','voice':'Thanh Bình','direction':'Day dứt và hoảng hốt; lời thoại nguyên bản rất ngắn.','lines':['Minh… bố xin lỗi. Bố chỉ cứu được một đứa.', 'Đứa nào là Minh?']},
 {'id':'06-tai-xe-thien-tam-duc','character':'Tài xế','voice':'Thiền Tâm Đức','direction':'Người lớn tuổi, dè chừng; kiểm tra độ phù hợp tuổi giọng.','lines':['Cậu xuống đây. Đi bộ mười phút.', 'Cậu có nhớ năm hai nghìn không trăm linh tám không?', 'Cậu đừng mang nó vào làng. Nếu họ biết có người đã ra được, họ sẽ tìm cậu.']},
]
out=ROOT/'storage/audio/previews/truyen-2/character-voices';out.mkdir(parents=True,exist_ok=True)
script=json.loads((ROOT/'storage/episodes/8/episode_script.json').read_text())
source=' '.join(p['text'] for s in script['sections'] for p in s['paragraphs'])
for s in samples:
 for line in s['lines']:
  assert line in source, f'Dialogue not in source: {line}'
(out/'casting.json').write_text(json.dumps({'narrator':'Thiện Minh','samples':samples,'paid_api':False,'note':'Direction describes the intended role; actual age and emotional delivery require listening review. No pitch shifts applied.'},ensure_ascii=False,indent=2))
print('Loading cached model OFFLINE; no paid services.',flush=True)
tts=Vieneu(mode='v3turbo',backend='onnx',device='cpu',precision='fp32',threads=4)
results=[]
for s in samples:
 start=time.monotonic();wav=out/(s['id']+'.wav');mp3=out/(s['id']+'.mp3')
 if not wav.exists():
  audio=tts.infer('\n\n'.join(s['lines']),voice=s['voice'],temperature=0.8,max_chars=256)
  assert len(audio)>48000 and np.isfinite(audio).all()
  tts.save(audio,str(wav))
 subprocess.run(['ffmpeg','-hide_banner','-nostdin','-y','-loglevel','error','-i',str(wav),'-af','loudnorm=I=-16:TP=-1.5:LRA=11','-c:a','libmp3lame','-b:a','160k',str(mp3)],check=True)
 info=sf.info(wav)
 item={'character':s['character'],'voice':s['voice'],'path':str(mp3),'duration_sec':round(info.duration,2),'seconds':round(time.monotonic()-start,1)}
 results.append(item);print(json.dumps(item,ensure_ascii=False),flush=True)
 (out/'report.json').write_text(json.dumps({'paid_api':False,'api_cost':0,'samples':results},ensure_ascii=False,indent=2))
print('DONE: six character voice samples.',flush=True)
