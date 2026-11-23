"""Checksum-verified local sound catalog; no downloads or paid generators."""
from pathlib import Path
import json
import numpy as np
from .io import identity,digest,stable_hash,dump,write,decode
from .settings import AudioSettings

AMBIENCE=['vietnamese_village_night','forest_night','haunted_house','heavy_rain','distant_thunder','graveyard','abandoned_temple','wind_through_bamboo']
SFX=['wooden_door_knock','slow_footsteps','door_creak','breathing','whisper','heartbeat','distant_scream','temple_bell','dog_howling','water_dripping']
MUSIC=['dark_ambient','low_drone','suspense_build','emotional_horror','supernatural_reveal','climax','haunting_outro']
TRANSITIONS=['low_boom','reverse_whoosh','dark_riser','eerie_stinger','sudden_silence','audio_signature']
ALLOWED_LICENSES={'CC0-1.0','CC-BY-4.0','CC-BY-SA-4.0','public-domain','original'}


def procedural(name: str, rate: int) -> np.ndarray:
    """Stylized synthesized textures, explicitly not authentic field recordings or spoken dialogue."""
    if name not in AMBIENCE+SFX+MUSIC+TRANSITIONS:
        raise ValueError('Unknown procedural asset: '+name)
    seconds=12 if name in AMBIENCE+MUSIC else 4
    n=int(seconds*rate);t=np.arange(n,dtype=np.float64)/rate
    rng=np.random.default_rng(int(stable_hash(name)[:8],16));noise=rng.normal(0,1,n)
    freqs=np.fft.rfftfreq(n,1/rate)
    cutoff=6500 if name=='heavy_rain' else 1800 if name in ('whisper','breathing') else 1000
    response=(freqs/np.maximum(freqs,80)) / (1+(freqs/cutoff)**4)
    colored=np.fft.irfft(np.fft.rfft(noise)*response,n).real
    colored/=max(np.std(colored),1e-9)
    if name in MUSIC:
        roots={'dark_ambient':55,'low_drone':49,'suspense_build':65.4,'emotional_horror':82.4,'supernatural_reveal':58.3,'climax':73.4,'haunting_outro':55}
        root=roots[name]
        a=.07*np.sin(2*np.pi*root*t)*(1+.2*np.sin(2*np.pi*.13*t))+.035*np.sin(2*np.pi*root*1.501*t)+.009*colored
        if name in ('climax','suspense_build'):a*=np.linspace(.4,1,n)
    elif name in AMBIENCE:
        a=.025*colored
        if name in ('forest_night','vietnamese_village_night','graveyard'):
            a+=.018*np.sin(2*np.pi*2600*t)*np.maximum(0,np.sin(2*np.pi*1.7*t))**12
        if name in ('wind_through_bamboo','haunted_house','abandoned_temple'):
            a*=.5+.4*np.sin(2*np.pi*.15*t)**2
        if name=='distant_thunder':a*=np.exp(-((t-4)/1.5)**2)
    elif name in ('wooden_door_knock','slow_footsteps','heartbeat','water_dripping'):
        a=np.zeros(n)
        for onset in ([.3,.8,1.3] if name=='wooden_door_knock' else [.25,.5,1.2,1.45,2.2,2.45,3.2,3.45] if name=='heartbeat' else [.3,1.2,2.1,3] if name=='slow_footsteps' else [.3,1.7,2.8]):
            dt=t-onset;env=np.where(dt>=0,np.exp(-np.maximum(dt,0)*(25 if name!='water_dripping' else 40)),0)
            freq=95 if name!='water_dripping' else 850
            a+=env*(.14*np.sin(2*np.pi*freq*dt)+.015*colored)
    elif name=='temple_bell':
        a=sum(.09/(i+1)*np.sin(2*np.pi*180*r*t)*np.exp(-t*(.8+i*.3)) for i,r in enumerate([1,2.71,4.1,5.43]))
    elif name in ('breathing','whisper','reverse_whoosh','dark_riser'):
        env=np.sin(np.pi*t/seconds)**2
        if name in ('reverse_whoosh','dark_riser'):env=(t/seconds)**2
        a=.05*colored*env
    elif name in ('door_creak','dog_howling','distant_scream'):
        freq=180+80*np.sin(np.pi*t/seconds)
        a=.08*np.sin(2*np.pi*np.cumsum(freq)/rate)*np.sin(np.pi*t/seconds)**2+.005*colored
    elif name=='sudden_silence':
        a=np.zeros(n)
    else:
        a=.12*np.sin(2*np.pi*(70*t-4*t*t))*np.exp(-t*2)+.015*colored*np.exp(-t*3)
        if name=='audio_signature':a+=.03*np.sin(2*np.pi*330*t)*np.exp(-t)
    fade=min(int(rate*.04),n//2);a[:fade]*=np.linspace(0,1,fade);a[-fade:]*=np.linspace(1,0,fade)
    # Procedural transients have deliberately bounded peaks before cue gain/ducking.
    peak=float(np.max(np.abs(a)))
    if peak>.22:a*=.22/peak
    return a.astype(np.float32)


class Catalog:
    def __init__(self, storage: Path, cfg: AudioSettings, project_root: Path):
        self.storage,self.cfg=storage,cfg
        p=Path(cfg.asset_catalog);p=p if p.is_absolute() else project_root/p
        self.entries=json.loads(p.read_text()).get('assets',{}) if p.exists() else {}
        self.project_root=project_root

    def resolve(self, asset_id: str) -> dict:
        entry=self.entries.get(asset_id)
        if entry:
            license=entry.get('license')
            if license not in ALLOWED_LICENSES:raise ValueError(f'Unverified sound asset license: {asset_id}')
            if license.startswith('CC-BY') and not entry.get('attribution'):raise ValueError('Attribution required: '+asset_id)
            p=Path(entry['path']);p=p if p.is_absolute() else self.project_root/p
            if not p.is_file() or not entry.get('sha256') or digest(p)!=entry['sha256']:raise ValueError('Sound asset missing or checksum mismatch: '+asset_id)
            decode(p)  # validate decode, not just extension
            # Copy into content-addressed storage so later source edits do not mutate a render.
            dest=self.storage/'assets/sound/cache'/f'{entry["sha256"]}{p.suffix}'
            dest.parent.mkdir(parents=True,exist_ok=True)
            if not dest.exists():
                import shutil
                shutil.copy2(p,dest)
            if digest(dest)!=entry['sha256']:raise ValueError('Cached sound asset checksum mismatch: '+asset_id)
            return {**entry,**identity(dest),'asset_id':asset_id,'fallback':False}
        if not self.cfg.procedural_fallback:raise ValueError('No licensed sound asset and fallback disabled: '+asset_id)
        key=stable_hash({'name':asset_id,'rate':self.cfg.sample_rate,'synth_version':1})[:20]
        p=self.storage/'assets/sound/procedural'/f'{asset_id}-{key}.wav';meta=p.with_suffix('.json')
        if not p.exists():write(p,procedural(asset_id,self.cfg.sample_rate),self.cfg.sample_rate)
        record={**identity(p),'asset_id':asset_id,'license':'CC0-1.0','source':'locally synthesized by Audio Story Studio procedural DSP v1',
                'attribution':'','fallback':True,'description':'Procedural approximation, not an authentic recording or intelligible speech'}
        dump(meta,record)
        return record
