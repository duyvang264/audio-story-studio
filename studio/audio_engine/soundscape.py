"""Narration-timed tension and sound cues; deliberate silence never deletes spoken words."""
from pydantic import BaseModel,Field,model_validator


class SoundCue(BaseModel):
    scene_id: int
    cue_id: str
    asset_id: str
    category: str
    stem: str
    trigger: str
    relative_position: float = Field(ge=0,le=1)
    start_ms: int = Field(ge=0)
    duration_ms: int = Field(gt=0)
    gain_db: float = Field(le=0,ge=-80)
    fade_in_ms: int = Field(ge=0)
    fade_out_ms: int = Field(ge=0)
    priority: int = Field(ge=0,le=10)
    emotional_intensity: float = Field(ge=0,le=5)
    silence_scope: str = 'background'
    pre_roll_ms: int = Field(0, ge=0)

    @model_validator(mode='after')
    def bounds(self):
        if self.fade_in_ms+self.fade_out_ms>self.duration_ms:raise ValueError('Cue fades exceed cue duration')
        if self.stem not in ('ambience','music','sfx','audio_signature','silence'):raise ValueError('Unknown audio stem')
        return self


KEYS={'wooden_door_knock':['gõ cửa','tiếng gõ'],'slow_footsteps':['bước chân'],'door_creak':['cửa mở','khép cửa','mở cửa'],
      'breathing':['ngừng thở','hơi thở'],'whisper':['thì thầm'],'heartbeat':['cổ họng','nghẹn','kinh hoàng'],
      'distant_scream':['hét','thét'],'temple_bell':['chuông đình','tiếng chuông'],'dog_howling':['chó tru'],
      'water_dripping':['giếng','giọt nước']}


def tension(position: float, text: str='') -> float:
    if position<.03:base=3.2
    elif position<.20:base=1.1
    elif position<.40:base=2.1
    elif position<.50:base=1.0  # false safety, not sustained maximum tension
    elif position<.75:base=2.6
    elif position<.875:base=3.5
    elif position<.96:base=4.4
    else:base=3.2
    hits=sum(word in text.lower() for word in ['sợ','chết','đừng','giếng','chuông','khóc','bóng tối'])
    return round(min(5,base+min(.6,hits*.12)),2)


def plan(script: dict, narration: dict, scenes: list[dict]) -> dict:
    total=float(narration['duration_sec']);cues=[];curve=[];last_event={}
    def cue(scene,name,stem,at,duration,gain,intensity,trigger):
        at=max(0,min(float(at),total-.02));duration=min(float(duration),total-at)
        if duration<=.01:return
        number=len(cues)+1
        fade=min(round(duration*1000/4),800 if stem in ('music','ambience') else 40)
        cues.append(SoundCue(scene_id=scene['scene_no'],cue_id=f'cue-{number:04d}',asset_id=name,category=name,stem=stem,
                             trigger=trigger,relative_position=max(0,min(1,(at-scene['start'])/max(scene['end']-scene['start'],.001))),
                             start_ms=round(at*1000),duration_ms=max(1,round(duration*1000)),gain_db=gain,
                             fade_in_ms=fade,fade_out_ms=fade,priority=9 if stem=='sfx' else 4,
                             emotional_intensity=intensity,pre_roll_ms=max(0,round((scene['start']-at)*1000))).model_dump())
    for scene in scenes:
        start,end=float(scene['start']),float(scene['end'])
        inside=[ch for ch in narration['chunks'] if ch['end']>start and ch['start']<end]
        text=' '.join(ch['text'] for ch in inside);low=text.lower();level=tension((start+end)/2/total,text)
        curve.append({'scene_id':scene['scene_no'],'start_ms':round(start*1000),'end_ms':round(end*1000),'intensity':level})
        ambient=('heavy_rain' if 'mưa' in low else 'wind_through_bamboo' if 'tre' in low else 'abandoned_temple' if 'đình' in low else
                 'haunted_house' if any(w in low for w in ['buồng','nhà','cửa','mâm cơm']) else 'forest_night' if 'rừng' in low else
                 'graveyard' if 'mồ' in low else 'vietnamese_village_night')
        overlap_start=max(0,start-.4);overlap_end=min(total,end+.4)
        cue(scene,ambient,'ambience',overlap_start,overlap_end-overlap_start,-16+level*.5,level,'scene_location_crossfade')
        music='climax' if level>=4.4 else 'suspense_build' if level>=3 else 'emotional_horror' if 'khóc' in low else 'dark_ambient'
        if end>=total-.1:music='haunting_outro'
        cue(scene,music,'music',overlap_start,overlap_end-overlap_start,-14+level*.6,level,'tension_curve_crossfade')
        for ch in inside:
            lower=ch['text'].lower()
            for name,keys in KEYS.items():
                if not any(k in lower for k in keys):continue
                if name=='temple_bell' and any(k in lower for k in ['không thu','không có âm thanh','không tồn tại','không đến từ','chỉ có mười hai']):continue
                # The supernatural thirteenth bell must not become an ordinary audible bell.
                if name=='temple_bell' and 'mười ba' in lower:continue
                at=max(start,ch['start'])
                if at-last_event.get(name,-999)<18:continue
                last_event[name]=at
                cue(scene,name,'sfx',at,min(3.8,end-at),-14,level,'narration_keyword:'+next(k for k in keys if k in lower))
        if start>0:
            cue(scene,'reverse_whoosh' if level<4 else 'low_boom','sfx',start,min(1.2,end-start),-20,level,'scene_transition')
        if .875<=start/total<.96:
            # Background silence only: never fabricate missing narration or mask a dropout.
            cue(scene,'sudden_silence','silence',start,.65,-80,level,'revelation_breath')
    if scenes:
        cue(scenes[0],'audio_signature','audio_signature',min(total*.03,35),min(3,total*.02),-16,2,'title_signature')
    for override in script.get('sound_cues',[]):
        item=SoundCue.model_validate(override).model_dump()
        if item['start_ms']+item['duration_ms']>total*1000+1:raise ValueError('Sound cue extends past narration')
        cues.append(item)
    return {'schema_version':1,'duration_ms':round(total*1000),'timing_source':'measured narration','tension_curve':curve,'cues':cues,
            'note':'Procedural fallbacks are stylized textures. Silence is background-only unless explicitly authored otherwise.'}
