"""Configurable horror beats, local-only draft generation and measured-duration validation."""
from pathlib import Path
import json,re
from urllib.parse import urlparse
from pydantic import BaseModel,Field,model_validator
import httpx
from .audio_engine.io import dump,stable_hash


class DraftSection(BaseModel):
    title: str = Field(min_length=1)
    paragraphs: list[str] = Field(min_length=1)


class StoryDraft(BaseModel):
    model_config = {'extra': 'allow'}
    title: str = Field(min_length=1)
    hook_candidates: list[str] = Field(min_length=3, max_length=3)
    sections: list[DraftSection] = Field(min_length=1)
    continuity: dict = Field(default_factory=dict)
    unresolved_questions: list[str] = Field(default_factory=list)


class StorySettings(BaseModel):
    target_duration_sec: float = Field(1200,gt=60)
    structure_duration_sec: float = Field(1200,gt=60)
    duration_tolerance_fraction: float = Field(.25,ge=0,lt=1)
    hook_first_sec: float = Field(5,gt=0)
    cold_open_end_sec: float = 35
    title_end_sec: float = 45
    story_end_sec: float = 1050
    climax_end_sec: float = 1155
    mini_hook_sec: list[float] = Field(default_factory=lambda:[300,600,900])
    teaser_enabled: bool = False
    strict_duration: bool = False
    allow_hook_rewrite: bool = True
    local_endpoint: str = 'http://127.0.0.1:11434/api/generate'
    local_model: str = 'qwen3:8b'
    generation_timeout_sec: float = 600
    duplicate_paragraph_fraction: float = Field(.2,ge=0,le=1)

    @model_validator(mode='after')
    def order(self):
        if not 0<self.cold_open_end_sec<self.title_end_sec<self.story_end_sec<self.climax_end_sec<self.structure_duration_sec:
            raise ValueError('Story beat timestamps must be ordered and within episode target')
        if urlparse(self.local_endpoint).hostname not in ('127.0.0.1','localhost','::1'):
            raise ValueError('Story generation is local-only; remote/paid endpoints are not allowed')
        return self


def hooks(text: str) -> list[dict]:
    candidates=[s.strip() for s in re.split(r'(?<=[.!?…])\s+',text) if s.strip()][:12]
    if not candidates:raise ValueError('No story text')
    scored=[]
    for sentence in candidates:
        low=sentence.lower();score=sum(k in low for k in ['chết','không','giọng','đừng','giếng','mất','hai','bố','mẹ'])
        score+=1 if 8<=len(sentence.split())<=30 else 0
        score-=5 if re.search(r'xin chào|chào mừng|đăng ký kênh',low) else 0
        scored.append({'text':sentence,'score':score})
    # Candidates are source excerpts; no invented facts, no unsolicited rewriting of existing stories.
    return sorted(scored,key=lambda x:-x['score'])[:3]


def schedule(cfg: StorySettings, actual_duration: float | None=None) -> list[dict]:
    scale=(actual_duration or cfg.target_duration_sec)/cfg.structure_duration_sec
    beats=[('cold_open',0,cfg.cold_open_end_sec),('title_reveal',cfg.cold_open_end_sec,cfg.title_end_sec),
           ('story',cfg.title_end_sec,cfg.story_end_sec),('climax',cfg.story_end_sec,cfg.climax_end_sec),
           ('cliffhanger',cfg.climax_end_sec,cfg.structure_duration_sec)]
    return [{'beat':name,'start_sec':round(a*scale,3),'end_sec':round(b*scale,3)} for name,a,b in beats]


def validate(script: dict, cfg: StorySettings, narration: dict | None=None, continuity: dict | None=None) -> dict:
    paragraphs=[p['text'] for sec in script.get('sections',[]) for p in sec.get('paragraphs',[])]
    text='\n\n'.join(paragraphs);issues=[]
    if not text.strip():issues.append({'severity':'critical','type':'empty_story'})
    if re.search(r'^(xin chào|chào mừng)',text.strip(),re.I):issues.append({'severity':'warning','type':'greeting_in_hook'})
    norm=[re.sub(r'\W+','',p.lower()) for p in paragraphs];duplicates=(len(norm)-len(set(norm)))/max(len(norm),1)
    if duplicates>cfg.duplicate_paragraph_fraction:issues.append({'severity':'critical','type':'repetitive_paragraphs','fraction':duplicates})
    facts=script.get('continuity',{});previous=(continuity or {}).get('facts',{})
    for key,value in facts.items():
        if key in previous and previous[key]!=value and key not in script.get('continuity_updates',[]):
            issues.append({'severity':'critical','type':'continuity_conflict','fact':key,'before':previous[key],'after':value})
    actual=float(narration['duration_sec']) if narration else None
    if actual is not None and abs(actual-cfg.target_duration_sec)>cfg.target_duration_sec*cfg.duration_tolerance_fraction:
        issues.append({'severity':'critical' if cfg.strict_duration else 'warning','type':'duration_target','actual_sec':actual,'target_sec':cfg.target_duration_sec})
    candidates=hooks(text) if text.strip() else []
    return {'status':'FAIL' if any(i['severity']=='critical' for i in issues) else 'WARNING' if issues else 'PASS',
            'issues':issues,'hook_candidates':candidates,'selected_hook':candidates[0] if candidates else None,
            'hook_rewrite_applied':False,'schedule':schedule(cfg,actual),
            'mini_hooks_sec':[round(v*(actual or cfg.target_duration_sec)/cfg.structure_duration_sec,3) for v in cfg.mini_hook_sec],
            'continuity':{'facts':{**previous,**facts},'unresolved':script.get('unresolved_questions',[])},
            'note':'Existing authored narration is preserved. Schedule is an editorial timing target, not a claim that the text contains every beat; no stretching/padding to fake 20 minutes.'}


def generate(brief: dict, cfg: StorySettings, continuity: dict | None=None, client: httpx.Client | None=None) -> dict:
    prompt=('Viết kịch bản truyện kinh dị dân gian Việt Nam bằng tiếng Việt. Chỉ trả JSON: title, hook_candidates (đúng 3 câu mở), '
            'sections (mỗi phần có title, paragraphs là danh sách chuỗi), continuity là các sự kiện canon, unresolved_questions. '
            'Không chào kênh. Hook trong 5 giây đầu, title ngắn, mystery tăng dần, mini-hook ở phút 5/10/15, '
            'cao trào và cliffhanger có hệ quả. Không lặp lại lời giải thích, không tiết lộ hết bí mật mùa. '
            f'Thời lượng đích {cfg.target_duration_sec} giây, sẽ đo lại bằng TTS; không tự khẳng định thời lượng. '
            f'Brief: {json.dumps(brief,ensure_ascii=False)}. Canon: {json.dumps(continuity or {},ensure_ascii=False)}. '
            f'Beat plan: {json.dumps(schedule(cfg))}.')
    owned=client is None;client=client or httpx.Client(timeout=cfg.generation_timeout_sec,trust_env=False)
    try:
        response=client.post(cfg.local_endpoint,json={'model':cfg.local_model,'prompt':prompt,'stream':False,'format':'json'})
        response.raise_for_status();draft=StoryDraft.model_validate_json(response.json()['response']).model_dump()
    finally:
        if owned:client.close()
    if len(draft.get('hook_candidates',[]))!=3:raise ValueError('Local story model must return three hook candidates')
    if len(set(h.strip() for h in draft['hook_candidates']))!=3:raise ValueError('Hook candidates must be distinct')
    sections=[]
    for i,sec in enumerate(draft.get('sections',[]),1):
        pars=[{'paragraph_no':0,'text':p,'sentences':[p]} for p in sec.get('paragraphs',[]) if isinstance(p,str) and p.strip()]
        sections.append({'section_no':i,'chapter_title':sec['title'],'paragraphs':pars})
    pn=0
    for sec in sections:
        for p in sec['paragraphs']:pn+=1;p['paragraph_no']=pn
    script={**draft,'sections':sections};report=validate(script,cfg,continuity=continuity)
    candidates=[]
    for h in draft['hook_candidates']:
        candidates.extend(hooks(h))
    selected=max(candidates,key=lambda h:h['score'])
    if cfg.allow_hook_rewrite and sections and sections[0]['paragraphs']:
        sections[0]['paragraphs'][0]['text']=selected['text']
        sections[0]['paragraphs'][0]['sentences']=[selected['text']]
        report=validate(script,cfg,continuity=continuity)
    return {'script':script,'quality':report,'selected_hook':selected,'provider':'local-only','paid_api':False}


def continuity_file(script: dict, storage: Path) -> Path:
    series=script.get('series') or re.sub(r'\s*[—-]\s*Tập\s+\d+.*$','',script.get('story_title') or script.get('title',''),flags=re.I)
    return storage/'stories/continuity'/(stable_hash(series)[:20]+'.json')


def load_continuity(script: dict, storage: Path) -> dict:
    path=continuity_file(script,storage)
    return json.loads(path.read_text()) if path.exists() else {}


def commit_continuity(script: dict, storage: Path, cfg: StorySettings) -> None:
    """Only called after explicit content approval; publish approval remains separate."""
    if not script.get('continuity'):return
    previous=load_continuity(script,storage);r=validate(script,cfg,continuity=previous)
    if any(i['type']=='continuity_conflict' for i in r['issues']):raise ValueError('Continuity changed since validation; approve explicit continuity_updates first')
    dump(continuity_file(script,storage),{**r['continuity'],'last_episode_id':script.get('episode_id'),'source_script_digest':stable_hash(script)})


def export_draft(result: dict, out: Path, rights: str='unknown') -> Path:
    """Reusable draft export for CLI/worker; deterministic identity and pending import rights."""
    import yaml
    script=result['script'];dump(out,result)
    meta={'title':script['title'],'slug':'generated-'+stable_hash(script)[:20],'rights':rights,
          'continuity':script.get('continuity',{}),'unresolved_questions':script.get('unresolved_questions',[])}
    for key in ('series','continuity_updates','characters','locations','voice_cast','sound_cues'):
        if key in script:meta[key]=script[key]
    lines=['---',yaml.safe_dump(meta,allow_unicode=True,sort_keys=False).strip(),'---','']
    for sec in script['sections']:
        lines+=['## '+sec['chapter_title'],'']+[p['text']+'\n' for p in sec['paragraphs']]
    path=out.with_suffix('.md');path.write_text('\n'.join(lines),encoding='utf-8');return path
