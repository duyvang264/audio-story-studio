"""Isolated VieNeu batch process; offline model cache only, never a paid API."""
from pathlib import Path
import json,os,subprocess,sys
ROOT=Path(__file__).resolve().parents[2]
os.environ.update({'HF_HOME':str(ROOT/'storage/models/vieneu'),'HF_HUB_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1','XDG_CACHE_HOME':str(ROOT/'storage/tools/vieneu/cache')})
import numpy as np
import soundfile as sf
from vieneu import Vieneu

def main():
    request=json.loads(Path(sys.argv[1]).read_text());tts=Vieneu(mode='v3turbo',backend='onnx',device='cpu',precision='fp32',threads=4)
    for item in request['items']:
        path=Path(item['path']);native=path.with_suffix('.source.wav');path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():continue
        audio=tts.infer(item['text'],voice=request['voice'],temperature=.7,max_chars=220)
        if len(audio)<48000*.15 or not np.isfinite(audio).all():raise ValueError('Invalid VieNeu output')
        # Preserve model floats BEFORE PCM quantization or any gain adjustment.
        if not native.exists():sf.write(native,audio,48000,subtype='FLOAT')
        peak=float(np.max(np.abs(audio)));gain=min(1,.98/max(peak,1e-9))
        temp=path.with_suffix('.part.wav')
        subprocess.run(['ffmpeg','-hide_banner','-nostdin','-y','-loglevel','error','-i',str(native),'-af',f'volume={gain}',
                        '-ar','24000','-ac','1','-c:a','pcm_s16le',str(temp)],check=True,capture_output=True)
        temp.replace(path)

if __name__=='__main__':main()
