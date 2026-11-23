"""Bounded repairs for high-confidence narration defects. No source declipping claims."""
from pathlib import Path
import json
import numpy as np
from .. import media
from .analyzer import analyze, Context
from .settings import AudioSettings
from . import ENGINE_VERSION
from .io import decode, write, identity, stable_hash, dump


def repair(path: Path | str, out_dir: Path, cfg: AudioSettings | None=None, context: Context | None=None) -> dict:
    cfg,ctx,path=cfg or AudioSettings(),context or Context(),Path(path)
    key=stable_hash({"source":identity(path),"config":cfg.model_dump(),"context":ctx.__dict__,"repair_version":ENGINE_VERSION})[:20]
    root=out_dir/key;root.mkdir(parents=True,exist_ok=True);record=root/"repair_history.json"
    if record.exists():
        r=json.loads(record.read_text())
        if Path(r["output"]).is_file() and identity(r["output"])["sha256"]==r["output_sha256"]:
            return r
    before=analyze(path,cfg,ctx,root/"before")
    current,best,attempts=path,before,[]
    if before["metrics"].get("decode") and not any(f["severity"]=="critical" for f in before["flags"]):
        for attempt in range(1,cfg.max_auto_repair_attempts+1):
            actionable=[]
            for f in best["flags"]:
                kind=f["defect_type"]
                if ((kind=="dc_offset" and cfg.auto_remove_dc) or
                    (kind in ("click_candidate","splice_click") and cfg.auto_repair_clicks and f["confidence"]>=.9 and ctx.role in ("source","narration")) or
                    (kind=="rumble_candidate" and cfg.conditional_highpass and f["confidence"]>=.85) or
                    (kind=="stationary_noise_candidate" and cfg.conditional_denoise)):
                    actionable.append(f)
                elif (kind=='harshness_candidate' and cfg.conditional_deess) or (kind=='wide_dynamics' and cfg.conditional_compression):
                    actionable.append(f)
            if not actionable:
                break
            a,sr=decode(current);filters=[];actions=[]
            for f in actionable:
                kind=f["defect_type"]
                if kind=="dc_offset":
                    a-=np.mean(a,axis=0,keepdims=True);actions.append("subtract_measured_dc")
                elif kind=="click_candidate":
                    index=f["measurements"]["sample_index"]
                    if 1<=index<len(a)-1:
                        a[index]=(a[index-1]+a[index+1])/2;actions.append(f"interpolate_sample:{index}")
                elif kind=="splice_click":
                    i=round((f["start_ms"]+f["end_ms"])/2000*sr);n=min(round(.004*sr),i,len(a)-i)
                    if n>0:
                        a[i-n:i]*=np.linspace(1,0,n)[:,None];a[i:i+n]*=np.linspace(0,1,n)[:,None];actions.append("boundary_fade_4ms")
                elif kind=="rumble_candidate":
                    filters.append("highpass=f=55");actions.append("conditional_highpass_55hz")
                elif kind=="stationary_noise_candidate":
                    filters.append("afftdn=nr=3:nf=-55:tn=1");actions.append("optional_conservative_denoise_3db")
                elif kind=='harshness_candidate':
                    filters.append('deesser=i=0.1:m=0.3');actions.append('optional_measured_deess')
                elif kind=='wide_dynamics':
                    filters.append('acompressor=threshold=0.125:ratio=1.5:attack=20:release=250:makeup=1');actions.append('optional_gentle_compression')
            candidate=root/f"attempt-{attempt}.wav";write(candidate,a,sr)
            if filters:
                filtered=root/f"attempt-{attempt}-filtered.wav"
                media.ffmpeg(["-i",str(candidate),"-af",",".join(filters),"-c:a","pcm_s24le",str(filtered)])
                candidate=filtered
            after=analyze(candidate,cfg,ctx,root/f"after-{attempt}")
            old_critical=sum(f["severity"]=="critical" for f in best["flags"])
            new_critical=sum(f["severity"]=="critical" for f in after["flags"])
            old_clicks=sum(f["defect_type"] in ("click_candidate","splice_click") for f in best["flags"])
            new_clicks=sum(f["defect_type"] in ("click_candidate","splice_click") for f in after["flags"])
            worse=(new_critical>old_critical or new_clicks>old_clicks or
                   abs(after["metrics"]["duration_sec"]-before["metrics"]["duration_sec"])>.001 or
                   after["metrics"]["sample_peak_dbfs"]>before["metrics"]["sample_peak_dbfs"]+1)
            attempts.append({"attempt":attempt,"actions":actions,"output":str(candidate),"status":after["status"],"quality_worsened":worse})
            if worse:
                break
            current,best=candidate,after
    result={"source":identity(path),"output":str(current.resolve()),"output_sha256":identity(current)["sha256"],
            "before_status":before["status"],"after_status":best["status"],"before_metrics":before["metrics"],"after_metrics":best["metrics"],
            "attempts":attempts,"max_attempts":cfg.max_auto_repair_attempts,
            "note":"Source clipping and uncertain distortion require regeneration/listening. Normalization is not declipping. Originals are immutable."}
    dump(record,result)
    return result
