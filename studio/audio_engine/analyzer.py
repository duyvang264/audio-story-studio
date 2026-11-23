"""Multi-metric local QC. Heuristics identify candidates, not proof of perceptual quality."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from pathlib import Path
import json
import math
import re
import numpy as np
from .. import media
from .settings import AudioSettings
from . import ENGINE_VERSION
from .io import decode, identity, dump, db, stable_hash


@dataclass
class Context:
    role: str = "source"  # source, narration, premaster, master, encoded
    expected_duration: float | None = None
    timeline: dict | None = None
    cues: list[dict] = field(default_factory=list)
    intentional_silences: list[tuple[float, float]] = field(default_factory=list)
    boundaries: list[float] = field(default_factory=list)
    require_listening_review: bool = False
    dependencies: list[dict] = field(default_factory=list)


def flag(kind, start, end, severity, confidence, measurements, action):
    return {"defect_type": kind, "start_ms": round(max(0, start) * 1000),
            "end_ms": round(max(start, end) * 1000), "severity": severity,
            "confidence": confidence, "measurements": measurements,
            "proposed_action": action, "repair_status": "unrepaired"}


def loudness(path: Path | str, cfg: AudioSettings) -> dict:
    log = media.analyze(path, f"loudnorm=I={cfg.target_lufs}:TP={cfg.max_true_peak_dbtp}:LRA={cfg.target_lra_lu}:print_format=json", audio=True)
    matches = re.findall(r'\{\s*"input_i".*?\}', log, re.S)
    if not matches:
        raise media.MediaError("FFmpeg did not return loudness measurements")
    values = json.loads(matches[-1])
    def number(key):
        x = float(values[key])
        return x if math.isfinite(x) else None
    return {"integrated_lufs": number("input_i"), "true_peak_dbtp": number("input_tp"),
            "lra_lu": number("input_lra"), "threshold_lufs": number("input_thresh"),
            "target_offset": number("target_offset")}


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    edges = np.diff(np.r_[False, mask, False].astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist()))


def _subtract(a, b, exempt):
    parts = [(a, b)]
    for left, right in exempt:
        parts = [(x, y) for start, end in parts for x, y in
                 [(start, min(end, left)), (max(start, right), end)] if y > x]
    return parts


def _expected(timeline: dict | None):
    chunks = (timeline or {}).get("chunks", [])
    pauses = []
    for i, ch in enumerate(chunks):
        end = float(ch["end"])
        nxt = float(chunks[i + 1]["start"]) if i + 1 < len(chunks) else float((timeline or {}).get("duration_sec", end))
        pauses.append((max(0, end - .025), nxt + .025))
    if chunks:
        pauses.append((0, float(chunks[0]["start"]) + .025))
    return chunks, pauses


def timeline_defects(timeline: dict | None, duration: float, tolerance: float) -> list[dict]:
    if not timeline:
        return []
    out = []
    chunks, _ = _expected(timeline)
    if not chunks:
        return [flag("missing_narration", 0, duration, "critical", 1, {}, "Generate narration with measured timing")]
    last = 0.0
    for ch in chunks:
        a, b = float(ch["start"]), float(ch["end"])
        if a < last - .01 or b <= a or b > duration + tolerance:
            out.append(flag("narration_timeline", a, b, "critical", 1,
                            {"previous_end": last, "file_duration": duration}, "Rebuild from measured source chunks"))
        path = ch.get("audio_path")
        if path and not Path(path).is_file():
            out.append(flag("missing_tts_chunk", a, b, "critical", 1, {"chunk_no": ch.get("chunk_no")}, "Regenerate missing chunk; never substitute silence"))
        last = max(last, b)
    return out


def _spectral(mono, sr, frames, rms, cfg):
    """Measure low-energy nonzero frames only; voiced whispers are not assumed to be noise."""
    length = 8192
    valid = np.flatnonzero((rms > 1e-6) & (rms <= np.percentile(rms[rms > 1e-6], 25))) if np.any(rms > 1e-6) else []
    if len(valid) < 5 or len(mono) < length:
        return {"available": False}
    selected = np.asarray(valid)[np.linspace(0, len(valid)-1, min(128, len(valid))).astype(int)]
    spectra = []
    for ix in selected:
        start = min(int(ix * frames), len(mono) - length)
        block = mono[start:start+length] * np.hanning(length)
        spectra.append(np.abs(np.fft.rfft(block)) ** 2)
    power = np.mean(spectra, axis=0) + 1e-25
    freqs = np.fft.rfftfreq(length, 1/sr)
    band = (freqs >= 30) & (freqs <= min(16000, sr/2))
    total = float(power[band].sum()) + 1e-20
    hum = np.zeros(len(freqs), dtype=bool)
    for base in (50, 60):
        for harmonic in range(1, 5):
            hum |= np.abs(freqs - base*harmonic) <= sr/length
    flatness = float(np.exp(np.log(power[band]).mean()) / power[band].mean())
    return {"available": True, "quiet_rms_dbfs": db(float(np.sqrt(np.mean(rms[selected] ** 2)))),
            "spectral_flatness": flatness, "hiss_ratio": float(power[freqs >= 6000].sum()/total),
            "hum_ratio": float(power[hum].sum()/total),
            "rumble_ratio": float(power[freqs < 50].sum()/total)}


def analyze(path: Path | str, cfg: AudioSettings | None = None, context: Context | None = None,
            out_dir: Path | None = None) -> dict:
    cfg, ctx, path = cfg or AudioSettings(), context or Context(), Path(path)
    flags, metrics = [], {}
    try:
        source = identity(path)
        probe = media.probe(path)
        stream = next(s for s in probe["streams"] if s["codec_type"] == "audio")
        a, sr = decode(path)
    except (OSError, ValueError, StopIteration, media.MediaError) as exc:
        report = {"schema_version": 1, "engine_version":ENGINE_VERSION, "status": "FAIL", "passed": False, "input": {"path": str(path.resolve())},
                  "role": ctx.role, "metrics": {"decode": False}, "flags": [flag("decode", 0, 0, "critical", 1,
                  {"error": str(exc)[:500]}, "Replace or regenerate invalid source")], "dependencies": ctx.dependencies,
                  "analysis_config": cfg.model_dump(), "limitations": ["Automated QC cannot prove the absence of audible distortion or pronunciation errors."]}
        if out_dir:
            write_reports(report, out_dir, cfg)
        return report
    duration = len(a)/sr
    mono = a.mean(axis=1)
    absolute = np.abs(a)
    peak = float(absolute.max())
    near = absolute >= cfg.clip_threshold
    flat_pairs = (np.abs(np.diff(a, axis=0)) < 1e-7) & near[1:] & near[:-1]
    flat_runs = _runs(flat_pairs.any(axis=1))
    critical_runs = [(x, y+1) for x, y in flat_runs if y-x+1 >= cfg.critical_flat_samples]
    metrics.update({"decode": True, "sample_rate": sr, "channels": a.shape[1], "codec": stream["codec_name"],
                    "bit_depth": int(stream.get("bits_per_raw_sample") or stream.get("bits_per_sample") or 0),
                    "duration_sec": duration, "sample_peak_dbfs": db(peak), "clipped_sample_ratio": float(near.mean()),
                    "flat_topped_samples": sum(y-x for x, y in critical_runs), "dc_offset": float(np.mean(a)),
                    "rms_dbfs": db(float(np.sqrt(np.mean(a*a)))), "crest_db": db(peak)-db(float(np.sqrt(np.mean(a*a))))})
    plateau = float(np.mean((np.abs(np.diff(mono)) < 1e-7) & (np.abs(mono[1:]) > .05))) if len(mono)>1 else 0
    metrics["nonzero_plateau_ratio"] = plateau
    if plateau > .08 and metrics["crest_db"] < 5 and not critical_runs:
        flags.append(flag("saturation_candidate",0,duration,"review",.8,{"plateau_ratio":plateau,"crest_db":metrics["crest_db"]},
                          "Possible gain-reduced clipped source or nonlinear distortion; listen/regenerate, do not normalize away evidence"))
    for x, y in critical_runs[:32]:
        flags.append(flag("source_clipping" if ctx.role in ("source", "narration") else "clipping", x/sr, y/sr,
                          "critical", .99, {"flat_samples": y-x}, "Regenerate source or rerender from unclipped stems; normalization cannot restore clipped samples"))
    if not critical_runs and peak >= cfg.clip_threshold:
        flags.append(flag("near_clipping", 0, duration, "warning", .7,
                          {"ratio": metrics["clipped_sample_ratio"]}, "Inspect peak transients and gain staging"))
    if abs(metrics["dc_offset"]) > .003:
        flags.append(flag("dc_offset", 0, duration, "warning", .95, {"offset": metrics["dc_offset"]}, "Subtract measured DC offset"))
    frame = max(1, round(sr*.02))
    n = len(mono)//frame
    blocks = mono[:n*frame].reshape(n, frame) if n else mono[None, :]
    rms = np.sqrt(np.mean(blocks**2, axis=1))
    silence_mask = rms < 10**(cfg.silence_threshold_db/20)
    silences = [(x*.02, min(y*.02, duration)) for x, y in _runs(silence_mask)]
    metrics["silence_intervals"] = [[round(x,3), round(y,3)] for x,y in silences if y-x >= .2]
    chunks, planned_pauses = _expected(ctx.timeline)
    exempt = [*ctx.intentional_silences, *planned_pauses]
    for cue in ctx.cues:
        if cue.get("category") == "sudden_silence" and cue.get("silence_scope") == "all":
            exempt.append((cue["start_ms"]/1000, (cue["start_ms"]+cue["duration_ms"])/1000))
    for start, end in silences:
        for x, y in _subtract(start, end, exempt):
            # Leading/trailing safety padding is limited, never an exemption for missing words.
            expected_speech = any(max(x, c["start"]) < min(y, c["end"]) for c in chunks)
            threshold = cfg.max_unexpected_silence_sec
            if y-x > threshold:
                flags.append(flag("missing_narration" if expected_speech else "unexpected_silence", x, y, "critical", .95,
                                  {"duration_sec": y-x}, "Check source/timeline; regenerate missing speech with explicit authorization"))
    for ch in chunks:
        start,end=max(0,int(ch['start']*sr)),min(len(mono),int(ch['end']*sr))
        if end-start>sr*.2 and np.max(np.abs(mono[start:end]))<10**(cfg.silence_threshold_db/20):
            flags.append(flag('missing_narration',ch['start'],ch['end'],'critical',.99,
                              {'chunk_no':ch.get('chunk_no')},'Entire expected narration chunk is silent; regenerate rather than fabricate speech'))
    metrics["spectral"] = spectral = _spectral(mono, sr, frame, rms, cfg)
    positive=rms[rms>1e-6]
    if len(positive)>5:
        metrics['frame_dynamic_range_db']=db(float(np.percentile(positive,90)))-db(float(np.percentile(positive,10)))
        if ctx.role in ('source','narration') and metrics['crest_db']>26 and metrics['frame_dynamic_range_db']>18:
            flags.append(flag('wide_dynamics',0,duration,'warning',.7,{'crest_db':metrics['crest_db'],'range_db':metrics['frame_dynamic_range_db']},'Consider gentle compression only if intentional whisper/loud passages remain intelligible'))
        if ctx.role in ('source','narration') and spectral.get('available') and spectral['hiss_ratio']>.65 and spectral['quiet_rms_dbfs']>-38:
            flags.append(flag('harshness_candidate',0,duration,'review',.6,spectral,'Listen before gentle de-essing; do not dull consonants by default'))
    if ctx.role in ("source", "narration") and spectral.get("available"):
        if spectral["hum_ratio"] > cfg.hum_ratio_warning:
            flags.append(flag("hum_candidate", 0, duration, "review", .65, spectral,
                              "Listen to low-level regions; do not remove voiced fundamental frequencies"))
        if spectral["rumble_ratio"] > cfg.rumble_ratio_warning:
            confidence=.9 if spectral['rumble_ratio']>.85 and spectral['quiet_rms_dbfs']>-42 and spectral['hum_ratio']<.1 else .7
            flags.append(flag("rumble_candidate", 0, duration, "review", confidence, spectral, "Consider gentle high-pass only after assessment"))
        if spectral["quiet_rms_dbfs"] > cfg.noise_floor_warning_db and spectral["spectral_flatness"] > .25 and spectral["hiss_ratio"] > .2:
            flags.append(flag("stationary_noise_candidate", 0, duration, "review", .7, spectral,
                              "Compare gentle denoise against original; preserve whispers"))
    # Isolated one-sample impulses: opposing jumps plus return to the local waveform.
    if len(mono) >= 4:
        dif = np.diff(mono)
        local = np.repeat(rms, frame)[:len(mono)-2]
        if len(local) < len(mono)-2:
            local = np.pad(local, (0,len(mono)-2-len(local)), mode="edge")
        limit = np.maximum(cfg.click_min_jump, cfg.click_adaptive_ratio*local)
        pos = np.flatnonzero((np.abs(dif[:-1]) > limit) & (np.abs(dif[1:]) > limit) &
                             (dif[:-1]*dif[1:] < 0) & (np.abs(dif[:-1]+dif[1:]) < np.abs(dif[:-1])*.2)) + 1
        transient_windows = [(c["start_ms"]/1000, (c["start_ms"]+c["duration_ms"])/1000)
                             for c in ctx.cues if c.get("stem") in ("sfx", "audio_signature")]
        clicks = []
        for index in pos:
            when = index/sr
            if ctx.role not in ("source", "narration") and any(x-.02 <= when <= y+.02 for x,y in transient_windows):
                if len(flags)<200:
                    flags.append(flag('transient_in_authored_sfx',when-.001,when+.001,'warning',.3,
                                      {'sample_index':int(index)},'Likely intended transient; source stems remain independently checked'))
                continue
            if clicks and when-clicks[-1] < .005:
                continue
            clicks.append(when)
            if len(clicks) <= 100:
                flags.append(flag("click_candidate", when-.001, when+.001, "review", .92,
                                  {"sample_index": int(index), "jump": float(abs(dif[index-1])), "adaptive_threshold": float(limit[index-1])},
                                  "Interpolate isolated sample only on narration; protect intentional SFX"))
        metrics["click_candidate_count"] = len(clicks)
        if len(clicks) > 100:
            flags.append(flag("crackling_candidate", 0, duration, "review", .8, {"count":len(clicks)}, "Regenerate or listen before broad processing"))
    for boundary in ctx.boundaries:
        i = round(boundary*sr)
        if 0 < i < len(mono) and abs(float(mono[i]-mono[i-1])) > .05:
            flags.append(flag("splice_click", boundary-.01, boundary+.01, "review", .95,
                              {"jump": abs(float(mono[i]-mono[i-1]))}, "Apply short fade at known chunk boundary"))
    # Persistent level changes within speech; speaker changes/cued horror transients are exempt.
    if ctx.role in ('source','narration') and len(mono)>=sr*3:
        size=max(1,round(sr*.5));count=len(mono)//size
        levels=20*np.log10(np.maximum(np.sqrt(np.mean(mono[:count*size].reshape(count,size)**2,axis=1)),1e-12))
        known=[*ctx.boundaries,*[c['start'] for c in chunks]]
        for i in range(2,len(levels)-2):
            when=i*.5;jump=float(levels[i]-levels[i-1])
            if min(levels[i],levels[i-1])>cfg.silence_threshold_db-3 and abs(jump)>18 and np.ptp(levels[i-2:i])<6 and np.ptp(levels[i:i+2])<6 and not any(abs(when-b)<.6 for b in known):
                flags.append(flag('unexplained_level_jump',when-.5,when+.5,'review',.65,{'delta_db':jump},'Listen for changed speaker/emotion versus unintended gain change'))
    flags.extend(timeline_defects(ctx.timeline, duration, cfg.sync_tolerance_ms/1000))
    if ctx.expected_duration is not None and abs(duration-ctx.expected_duration) > cfg.sync_tolerance_ms/1000:
        flags.append(flag("duration_mismatch", min(duration,ctx.expected_duration), max(duration,ctx.expected_duration), "critical",1,
                          {"actual":duration,"expected":ctx.expected_duration}, "Rebuild without truncating narration"))
    if a.shape[1] == 2:
        powers = np.sqrt(np.mean(a*a,axis=0))
        metrics["channel_balance_db"] = abs(db(powers[0])-db(powers[1]))
        if metrics["channel_balance_db"] > 6:
            flags.append(flag("channel_imbalance",0,duration,"review",.8,{"delta_db":metrics["channel_balance_db"]},"Inspect routing and intentional panning"))
        if np.std(a[:,0]) > 1e-8 and np.std(a[:,1]) > 1e-8:
            correlation = float(np.mean((a[:,0]-a[:,0].mean())*(a[:,1]-a[:,1].mean()))/(np.std(a[:,0])*np.std(a[:,1])))
            metrics["channel_correlation"] = correlation
            if correlation < -.5:
                flags.append(flag("phase_candidate",0,duration,"review",.9,{"correlation":correlation},"Check polarity and mono compatibility"))
    try:
        metrics.update(loudness(path,cfg))
    except media.MediaError as exc:
        flags.append(flag("loudness_measurement",0,duration,"critical",1,{"error":str(exc)},"Repair invalid stream"))
    if ctx.role in ("master", "encoded"):
        lufs, tp = metrics.get("integrated_lufs"), metrics.get("true_peak_dbtp")
        if lufs is None or abs(lufs-cfg.target_lufs)>cfg.loudness_tolerance_lu:
            flags.append(flag("loudness",0,duration,"critical",1,{"lufs":lufs,"target":cfg.target_lufs},"Remaster from premaster"))
        if tp is None or tp > cfg.max_true_peak_dbtp + .01:
            flags.append(flag("true_peak",0,duration,"critical",1,{"true_peak_dbtp":tp,"limit":cfg.max_true_peak_dbtp},"Reduce clean-source gain/headroom and encode again"))
        if sr != cfg.sample_rate or a.shape[1] != cfg.channels:
            flags.append(flag("master_format",0,duration,"critical",1,{"rate":sr,"channels":a.shape[1]},"Render configured output format"))
        if ctx.role=='master' and (stream['codec_name']!='pcm_s24le' or metrics['bit_depth']!=24):
            flags.append(flag('master_bit_depth',0,duration,'critical',1,{'codec':stream['codec_name'],'bit_depth':metrics['bit_depth']},'Export a lossless WAV PCM 24-bit master'))
    if ctx.role == "encoded":
        if stream['codec_name']!='aac':
            flags.append(flag('encoded_codec',0,duration,'critical',1,{'codec':stream['codec_name']},'Validate the actual AAC output'))
        video = next((st for st in probe["streams"] if st["codec_type"]=="video"),None)
        if video:
            av_offset = abs(float(stream.get("start_time",0))-float(video.get("start_time",0)))
            av_drift = abs(float(stream.get("duration",duration))-float(video.get("duration",duration)))
            metrics.update({"av_start_offset_ms": av_offset*1000,"av_duration_delta_ms":av_drift*1000})
            if max(av_offset,av_drift)>cfg.sync_tolerance_ms/1000:
                flags.append(flag("av_sync",0,duration,"critical",1,{"offset_ms":av_offset*1000,"drift_ms":av_drift*1000},"Rebuild aligned audio/video timeline"))
    if ctx.require_listening_review:
        flags.append(flag("perceptual_review",0,min(duration,30),"review",.5,{},"Listen for pronunciation, metallic voice texture and emotional delivery; record a file-bound review"))
    status = "FAIL" if any(f["severity"]=="critical" for f in flags) else "NEEDS_LISTENING_REVIEW" if any(f["severity"]=="review" for f in flags) else "WARNING" if flags else "PASS"
    report = {"schema_version":1,"engine_version":ENGINE_VERSION,"status":status,"passed":status in ("PASS","WARNING"),"input":source,"role":ctx.role,
              "metrics":metrics,"flags":flags,"dependencies":ctx.dependencies,"analysis_config":cfg.model_dump(),
              "context_digest":stable_hash(asdict(ctx)),"limitations":["No single metric proves audible distortion is absent.","Pronunciation and emotional realism require listening or separately validated ASR; numeric QC is not a pronunciation test."]}
    if out_dir:
        write_reports(report,out_dir,cfg)
    return report


def write_reports(report: dict, directory: Path, cfg: AudioSettings | None = None) -> None:
    directory.mkdir(parents=True,exist_ok=True)
    dump(directory/"audio_qc_report.json",report)
    dump(directory/"audio_metrics.json",report["metrics"])
    dump(directory/"flagged_segments.json",report["flags"])
    lines=[f"# Audio QC: {report['status']}","",f"Input: {report['input']['path']}","",*report["limitations"],"","| Defect | Interval (ms) | Severity | Confidence | Action |","|---|---|---|---|---|"]
    for f in report["flags"]:
        lines.append(f"| {f['defect_type']} | {f['start_ms']}–{f['end_ms']} | {f['severity']} | {f['confidence']} | {f['proposed_action']} |")
    (directory/"audio_qc_report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    cfg=cfg or AudioSettings()
    if cfg.export_review_clips and report["metrics"].get("decode"):
        clips=directory/"review_clips";clips.mkdir(exist_ok=True)
        for i,f in enumerate([f for f in report["flags"] if f["severity"] in ("critical","review")][:cfg.max_review_clips],1):
            start=max(0,f["start_ms"]/1000-1);duration=min(12,max(3,(f["end_ms"]-f["start_ms"])/1000+2))
            media.ffmpeg(["-ss",str(start),"-i",report["input"]["path"],"-t",str(duration),"-map","0:a:0","-vn","-c:a","pcm_s24le",str(clips/f"{i:02d}-{f['defect_type']}.wav")])
