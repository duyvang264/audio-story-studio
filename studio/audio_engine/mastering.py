"""One two-pass EBU R128 master shared by both renderers, with AAC overshoot headroom."""
from pathlib import Path
import json
from .. import media
from .analyzer import loudness,analyze,Context
from .settings import AudioSettings
from . import ENGINE_VERSION
from .io import identity,stable_hash,dump


def master(path: Path | str, out_dir: Path, cfg: AudioSettings | None=None) -> dict:
    cfg=cfg or AudioSettings();path=Path(path)
    key=stable_hash({"source":identity(path),"settings":cfg.model_dump(),"master_version":ENGINE_VERSION})[:20]
    root=out_dir/key;root.mkdir(parents=True,exist_ok=True);record=root/"mastering.json";out=root/"final_audio.wav"
    if record.exists() and out.exists():
        old=json.loads(record.read_text())
        if identity(out)["sha256"]==old.get("output",{}).get("sha256"):
            return old
    measured=loudness(path,cfg)
    if any(measured[k] is None for k in ("integrated_lufs","true_peak_dbtp","lra_lu","threshold_lufs","target_offset")):
        raise media.MediaError("Cannot master silent/invalid input")
    peak=cfg.max_true_peak_dbtp-cfg.codec_headroom_db
    filt=(f"loudnorm=I={cfg.target_lufs}:TP={peak}:LRA={cfg.target_lra_lu}:"
          f"measured_I={measured['integrated_lufs']}:measured_TP={measured['true_peak_dbtp']}:"
          f"measured_LRA={measured['lra_lu']}:measured_thresh={measured['threshold_lufs']}:"
          f"offset={measured['target_offset']}:linear=true:print_format=json")
    # Obtain target_offset for the SAME target TP used on pass 2.
    pass1=media.analyze(path,f"loudnorm=I={cfg.target_lufs}:TP={peak}:LRA={cfg.target_lra_lu}:print_format=json",audio=True)
    import re
    measurement=json.loads(re.findall(r'\{\s*"input_i".*?\}',pass1,re.S)[-1])
    filt=re.sub(r"offset=[^:]+",f"offset={measurement['target_offset']}",filt)
    tmp=out.with_name("final_audio.part.wav")
    media.ffmpeg(["-i",str(path),"-af",filt,"-ar",str(cfg.sample_rate),"-ac",str(cfg.channels),"-c:a","pcm_s24le",str(tmp)])
    tmp.replace(out)
    qc=analyze(out,cfg,Context(role="master",expected_duration=media.duration(path),dependencies=[identity(path)]),root/"qc")
    result={"source":identity(path),"output":identity(out),"two_pass":True,"normalization_filter":filt,
            "before_metrics":measured,"after_metrics":qc["metrics"],"status":qc["status"],"qc_report":str(root/"qc/audio_qc_report.json")}
    dump(record,result);return result
