"""Five-stem floating-point mix with narration-driven ducking and masking measurements."""
from pathlib import Path
import json
import numpy as np
from .io import decode,write,identity,stable_hash,dump,db
from .settings import AudioSettings
from .analyzer import flag


def envelope(narration: np.ndarray, rate: int, cfg: AudioSettings, curve: list[dict]) -> tuple[np.ndarray,np.ndarray,np.ndarray]:
    frame=max(1,round(rate*.02));count=(len(narration)+frame-1)//frame
    mono=narration.mean(axis=1)
    padded=np.pad(mono,(0,count*frame-len(mono)))
    rms=np.sqrt(np.mean(padded.reshape(count,frame)**2,axis=1))
    levels=np.ones(count,dtype=np.float32)
    for sc in curve:
        levels[max(0,int(sc['start_ms']/20)):min(count,int(np.ceil(sc['end_ms']/20)))]=sc['intensity']
    threshold=max(10**(-48/20),float(np.percentile(rms,85))*.04)
    active=rms>threshold
    amount=cfg.duck_min_db+(cfg.duck_max_db-cfg.duck_min_db)*levels/5
    target=np.where(active,10**(-amount/20),1.0)
    duck=np.ones(count,dtype=np.float32);state=1.0
    for i,want in enumerate(target):
        tau=cfg.duck_attack_ms if want<state else cfg.duck_release_ms
        alpha=np.exp(-20/tau);state=alpha*state+(1-alpha)*want;duck[i]=state
    return duck,rms,active


def _place(stem: np.ndarray, sample: np.ndarray, cue: dict, rate: int, duck: np.ndarray | None):
    start=round(cue['start_ms']/1000*rate);length=min(round(cue['duration_ms']/1000*rate),len(stem)-start)
    if length<=0 or not len(sample):return
    gain=10**(cue['gain_db']/20)
    # Process one-second blocks: do not tile a short loop across an entire episode in RAM.
    for offset in range(0,length,rate):
        n=min(rate,length-offset);indices=np.arange(offset,offset+n)
        a=sample[indices%len(sample)].copy()
        fades=np.ones(n,dtype=np.float32)
        fi=cue['fade_in_ms']/1000*rate;fo=cue['fade_out_ms']/1000*rate
        if fi:fades*=np.minimum(1,indices/fi)
        if fo:fades*=np.minimum(1,(length-1-indices)/fo)
        if duck is not None:
            fi_idx=np.minimum((start+indices)//max(1,round(rate*.02)),len(duck)-1)
            fades*=duck[fi_idx]
        stem[start+offset:start+offset+n]+=a*(fades*gain)[:,None]


def mix(narration_path: Path, sound_plan: dict, assets: dict[str,dict], out_dir: Path,
        cfg: AudioSettings | None=None) -> dict:
    cfg=cfg or AudioSettings();rate=cfg.sample_rate
    key=stable_hash({'source':identity(narration_path),'plan':sound_plan,'assets':assets,'config':cfg.model_dump(),'mix_version':2})[:20]
    root=out_dir/key;root.mkdir(parents=True,exist_ok=True);record=root/'mix.json'
    if record.exists():
        old=json.loads(record.read_text())
        if all(Path(p['path']).is_file() and identity(p['path'])['sha256']==p['sha256'] for p in old['outputs'].values()):return old
    narration,_=decode(narration_path,rate,cfg.channels)
    total=round(sound_plan['duration_ms']/1000*rate)
    if abs(len(narration)-total)>rate*.1:raise ValueError('Sound/narration duration mismatch; do not truncate narration')
    # Frame rounding padding only, never remove actual narration.
    if len(narration)<total:narration=np.pad(narration,((0,total-len(narration)),(0,0)))
    duck,rms,active=envelope(narration,rate,cfg,sound_plan.get('tension_curve',[]))
    outputs={};write(root/'narration.wav',narration,rate);outputs['narration']=identity(root/'narration.wav')
    premaster=narration.copy();masking={};flags=[];frame=max(1,round(rate*.02));cache={}
    for name in ('ambience','music','sfx','audio_signature'):
        stem=np.zeros_like(narration)
        for cue in sound_plan['cues']:
            if cue['stem']!=name:continue
            asset=assets[cue['asset_id']]
            if cue['asset_id'] not in cache:cache[cue['asset_id']]=decode(asset['path'],rate,cfg.channels)[0]
            if name=='sfx':
                # SFX duck as well: an intentional knock is not a license to mask dialogue.
                control=np.maximum(duck,10**(-cfg.sfx_duck_db/20)).astype(np.float32)
            else:control=duck
            _place(stem,cache[cue['asset_id']],cue,rate,control)
        for cue in sound_plan['cues']:
            if cue['stem']!='silence':continue
            a=round(cue['start_ms']/1000*rate);b=min(len(stem),a+round(cue['duration_ms']/1000*rate))
            stem[a:b]*=0  # narration is NEVER included in this operation
        count=len(stem)//frame
        power=np.sqrt(np.mean(stem[:count*frame].mean(axis=1).reshape(count,frame)**2,axis=1)) if count else np.zeros(0)
        mask=active[:count];relative=np.zeros(count)
        if count:
            relative=20*np.log10(np.maximum(power,1e-12)/np.maximum(rms[:count],1e-12))
        fraction=float(np.mean(relative[mask]>-cfg.masking_margin_db)) if np.any(mask) else 0
        from .analyzer import _runs
        runs=_runs(mask & (relative>-cfg.masking_margin_db)) if count else []
        longest=max((b-a for a,b in runs),default=0)*20
        masking[name]={'masked_fraction':fraction,'longest_masked_ms':longest,'p95_relative_db':float(np.percentile(relative[mask],95)) if np.any(mask) else -120}
        if fraction>cfg.max_masked_fraction or longest>cfg.max_masking_run_ms:
            flags.append(flag('narration_masking',0,len(stem)/rate,'critical',.95,{'stem':name,**masking[name]},'Reduce cue gain/ducking and rerender from stems'))
        path=root/f'{name}.wav';write(path,stem,rate);outputs[name]=identity(path);premaster+=stem
        del stem
    headroom=min(1,.7/max(float(np.abs(premaster).max()),1e-9));premaster*=headroom
    write(root/'premaster.wav',premaster,rate,'pcm_f32le');outputs['premaster']=identity(root/'premaster.wav')
    result={'source':identity(narration_path),'outputs':outputs,'premaster_gain_db':db(headroom),'mixing_format':'float32',
            'masking':masking,'flags':flags,'ducking':{'min_gain_db':db(float(duck.min())),'max_gain_db':db(float(duck.max())),
            'attack_ms':cfg.duck_attack_ms,'release_ms':cfg.duck_release_ms},'attributions':sorted({a['attribution'] for a in assets.values() if a.get('attribution')})}
    dump(record,result);return result
