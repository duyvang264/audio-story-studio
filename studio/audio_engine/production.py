"""File-based stages shared by the worker and offline CLI; keeps existing episode contracts."""
from dataclasses import replace
from pathlib import Path
import json
from .. import media
from ..config import Settings,PROJECT_ROOT
from ..horror_story import validate as validate_story
from ..visual.scene_planner import plan_scenes,SceneRules
from .analyzer import analyze,Context,write_reports,flag
from .repair import repair
from .mastering import master
from .mixing import mix
from .assets import Catalog
from .soundscape import plan as sound_plan
from .io import dump,decode,write,identity,digest,stable_hash
from .gate import AudioQCError,ListeningReviewRequired,dependency,assert_report


def _read(path):return json.loads(Path(path).read_text())
def directory(episode_id:int,s:Settings,episode_dir:Path|None=None)->Path:
    return episode_dir or s.storage/'episodes'/str(episode_id)
def _state(d):return _read(d/'audio_engine/state.json') if (d/'audio_engine/state.json').exists() else {}
def _save(d,**values):
    state={**_state(d),**values};dump(d/'audio_engine/state.json',state);return state

def _fail(report):
    if report['status']=='FAIL':raise AudioQCError('Audio QC FAIL: '+', '.join(sorted({f['defect_type'] for f in report['flags'] if f['severity']=='critical'})))


def source_qc(ep:int,s:Settings,episode_dir:Path|None=None)->dict:
    d=directory(ep,s,episode_dir);tr=_read(d/'transcript_timeline.json');cfg=s.audio
    path=Path(tr.get('source_audio_path') or tr['audio_path']);root=d/'audio_engine/qc/source'
    script=_read(d/'episode_script.json')
    from ..horror_story import load_continuity
    story=validate_story(script,s.story_engine,tr,load_continuity(script,s.storage))
    dump(d/'story_quality_report.json',story)
    synthetic_test=s.tts_provider=='tone' and tr.get('tts','').startswith('tone|')
    required=cfg.require_listening_review and not synthetic_test
    required=required or tr.get('pronunciation_review_required',False)
    ctx=Context(role='source',expected_duration=tr['duration_sec'],timeline=tr,
                boundaries=[v for c in tr['chunks'] for v in (c['start'],c['end'])],require_listening_review=required,
                dependencies=[dependency(d/'transcript_timeline.json'),dependency(d/'episode_script.json'),dependency(d/'story_quality_report.json')])
    result=analyze(path,cfg,ctx)
    if story['status']=='FAIL':
        result['flags'].append(flag('story_quality',0,tr['duration_sec'],'critical',1,{'issues':story['issues']},'Correct story/continuity before production'))
    sources={}
    for ch in tr['chunks']:
        raw=Path(ch['audio_path']) if ch.get('audio_path') else None
        if raw:
            native=raw.with_suffix('.source.wav')
            unpaced=raw.with_name(raw.name.replace('-paced.wav','.wav'))
            raw=native if native.exists() else unpaced if unpaced.exists() else raw
            if raw.is_file():sources.setdefault(str(raw),ch)
    summaries=[]
    for file,ch in sources.items():
        report=analyze(file,cfg,Context(role='source'))
        summaries.append({'input':report['input'],'chunk_no':ch['chunk_no'],'status':report['status'],'metrics':report['metrics'],'flags':report['flags']})
        result['dependencies'].append(dependency(Path(file)))
        for item in report['flags']:
            if item['severity'] not in ('critical','review'):continue
            # Source timing may precede tempo changes: map interval proportionally to this measured narration segment.
            span=max(report['metrics'].get('duration_sec',.001),.001)
            scale=(ch['end']-ch['start'])/span
            shifted={**item,'start_ms':round(ch['start']*1000+item['start_ms']*scale),
                     'end_ms':round(ch['start']*1000+item['end_ms']*scale),
                     'measurements':{**item['measurements'],'chunk_no':ch['chunk_no'],'source_path':file,'timing_mapping':'proportional to measured chunk'}}
            result['flags'].append(shifted)
    if any(f['severity']=='critical' for f in result['flags']):result.update(status='FAIL',passed=False)
    elif any(f['severity']=='review' for f in result['flags']):result.update(status='NEEDS_LISTENING_REVIEW',passed=False)
    dump(root/'source_chunks.json',summaries)
    result['dependencies'].append(dependency(root/'source_chunks.json'))
    write_reports(result,root,cfg)
    _save(d,source_audio=str(path.resolve()),source_report=str(root/'audio_qc_report.json'))
    return {'status':result['status'],'report':str(root/'audio_qc_report.json'),'chunks_analyzed':len(sources)}


def repair_narration(ep:int,s:Settings,episode_dir:Path|None=None)->dict:
    d=directory(ep,s,episode_dir);state=_state(d);tr=_read(d/'transcript_timeline.json')
    path=Path(state['source_audio']);result=repair(path,s.storage/'audio/episodes'/str(ep)/'repairs',s.audio,
                    Context(role='narration',timeline=tr,boundaries=[c['end'] for c in tr['chunks']]))
    key=stable_hash({'source':identity(result['output']),'config':s.audio.model_dump(),'v':1})[:20]
    clean=s.storage/'audio/episodes'/str(ep)/'engine'/key/'narration_clean.wav'
    if not clean.exists():
        a,rate=decode(result['output'],s.audio.sample_rate,1);write(clean,a,rate)
    history=d/'audio_engine/repair_history.json';dump(history,result)
    if any(not attempt['quality_worsened'] for attempt in result['attempts']):
        original=_read(state['source_report'])
        snapshot=Path(state['source_report']).with_name('source_before_repair.json')
        if not snapshot.exists():dump(snapshot,original)
        for finding in original['flags']:
            if finding['defect_type'] in ('click_candidate','splice_click','dc_offset') and finding['confidence']>=.9 and result['after_status'] in ('PASS','WARNING'):
                finding['repair_status']='repaired'
        if not any(item['path']==str(history.resolve()) for item in original['dependencies']):
            original['dependencies'].append(dependency(history))
        write_reports(original,Path(state['source_report']).parent,s.audio)
    _save(d,clean_audio=str(clean),repair_history=str(history))
    return {'audio':str(clean),'attempts':len(result['attempts']),'before':result['before_status'],'after':result['after_status']}


def narration_qc(ep:int,s:Settings,episode_dir:Path|None=None)->dict:
    d=directory(ep,s,episode_dir);state=_state(d);tr=_read(d/'transcript_timeline.json');root=d/'audio_engine/qc/narration'
    r=analyze(state['clean_audio'],s.audio,Context(role='narration',timeline=tr,expected_duration=tr['duration_sec'],
              dependencies=[dependency(Path(state['source_report']),'report'),dependency(Path(state['repair_history']))]),root)
    _save(d,narration_report=str(root/'audio_qc_report.json'));_fail(r)
    # A critical original-source failure can never be hidden by processing/normalization.
    _fail(_read(state['source_report']))
    return {'status':r['status'],'report':str(root/'audio_qc_report.json')}


def resolve_sounds(ep:int,s:Settings,episode_dir:Path|None=None)->dict:
    d=directory(ep,s,episode_dir);tr=_read(d/'transcript_timeline.json');script=_read(d/'episode_script.json')
    scenes=None
    if (d/'scene_plan.json').exists():
        existing=_read(d/'scene_plan.json')
        if abs(existing.get('duration_sec',-999)-tr['duration_sec'])<.1:scenes=existing['scenes']
    if not scenes:scenes=plan_scenes(script,tr,SceneRules(s.target_scene_duration_sec,s.min_scene_duration_sec,s.max_scene_duration_sec))
    plan=sound_plan(script,tr,scenes)
    if not s.audio.sound_design_enabled:plan['cues']=[]
    cat=Catalog(s.storage,s.audio,PROJECT_ROOT);resolved={}
    for cue in plan['cues']:
        if cue['stem']!='silence' and cue['asset_id'] not in resolved:
            resolved[cue['asset_id']]=cat.resolve(cue['asset_id'])
    dump(d/'sound_cues.json',plan);dump(d/'sound_assets_resolved.json',resolved)
    return {'cues':len(plan['cues']),'assets':len(resolved),'procedural_fallbacks':sum(a['fallback'] for a in resolved.values())}


def sound_timeline(ep:int,s:Settings,episode_dir:Path|None=None)->dict:
    d=directory(ep,s,episode_dir);plan=_read(d/'sound_cues.json');tr=_read(d/'transcript_timeline.json')
    for cue in plan['cues']:
        if cue['start_ms']+cue['duration_ms']>tr['duration_sec']*1000+1:raise AudioQCError('Sound cue extends beyond real narration')
    dump(d/'scene_audio_timeline.json',{'duration_ms':plan['duration_ms'],'tension_curve':plan['tension_curve'],'cues':plan['cues']})
    return {'cues':len(plan['cues']),'timing_source':'actual narration'}


def mix_episode(ep:int,s:Settings,episode_dir:Path|None=None)->dict:
    d=directory(ep,s,episode_dir);state=_state(d);plan=_read(d/'sound_cues.json');assets=_read(d/'sound_assets_resolved.json')
    result=mix(Path(state['clean_audio']),plan,assets,s.storage/'audio/episodes'/str(ep)/'mixes',s.audio)
    root=d/'audio_engine/qc/premaster';tr=_read(d/'transcript_timeline.json')
    deps=[dependency(Path(state['narration_report']),'report'),dependency(d/'sound_cues.json'),dependency(d/'sound_assets_resolved.json')]
    deps += [dependency(Path(a['path'])) for a in assets.values()]
    r=analyze(result['outputs']['premaster']['path'],s.audio,Context(role='premaster',timeline=tr,cues=plan['cues'],expected_duration=tr['duration_sec'],dependencies=deps))
    r['flags'].extend(result['flags'])
    if result['flags']:r.update(status='FAIL',passed=False)
    r['metrics']['masking']=result['masking'];r['metrics']['ducking']=result['ducking'];write_reports(r,root,s.audio)
    manifest=d/'audio_engine/mix.json';dump(manifest,result)
    _save(d,mix=str(manifest),premaster=result['outputs']['premaster']['path'],premaster_report=str(root/'audio_qc_report.json'))
    _fail(r)
    return {'status':r['status'],'premaster':result['outputs']['premaster']['path'],'stems':list(result['outputs'])}


def master_episode(ep:int,s:Settings,episode_dir:Path|None=None)->dict:
    d=directory(ep,s,episode_dir);state=_state(d)
    result=master(state['premaster'],s.storage/'audio/episodes'/str(ep)/'masters',s.audio)
    _save(d,master_audio=result['output']['path'],mastering=result)
    return {'audio':result['output']['path'],'status':result['status']}


def master_qc(ep:int,s:Settings,episode_dir:Path|None=None)->dict:
    d=directory(ep,s,episode_dir);state=_state(d);plan=_read(d/'sound_cues.json');tr=_read(d/'transcript_timeline.json');root=d/'audio_engine/qc/master'
    r=analyze(state['master_audio'],s.audio,Context(role='master',timeline=tr,cues=plan['cues'],expected_duration=tr['duration_sec'],
              dependencies=[dependency(Path(state['premaster_report']),'report')]),root)
    _save(d,master_report=str(root/'audio_qc_report.json'));_fail(r)
    return {'status':r['status'],'report':str(root/'audio_qc_report.json')}


def audio_fields(d:Path)->dict:
    state=_state(d)
    if not state.get('master_audio') or not state.get('master_report'):raise AudioQCError('Audio master/QC missing; run audio production stages first')
    # Numeric failures stop rendering; uncertain sources may be rendered FOR REVIEW, never published.
    assert_report(state['master_report'],state['master_audio'],allow_review=True)
    return {'master':state['master_audio'],'master_qc':state['master_report'],'clean_narration':state['clean_audio'],'sound_cues':str(d/'sound_cues.json'),'bgm':None}


def prepare_episode(ep:int,s:Settings,episode_dir:Path|None=None)->dict:
    d=directory(ep,s,episode_dir);results={}
    for name,fn in [('tts_qc',source_qc),('audio_repair',repair_narration),('narration_qc',narration_qc),('sound_assets',resolve_sounds),('sound_timeline',sound_timeline),('audio_mix',mix_episode),('audio_master',master_episode),('master_audio_qc',master_qc)]:
        results[name]=fn(ep,s,d)
    if (d/'timeline.json').exists():
        t=_read(d/'timeline.json');t['audio'].update(audio_fields(d));dump(d/'timeline.json',t)
    dump(d/'audio_engine/run.json',results);return results


def encoded_qc(path:Path,d:Path,s:Settings,*,short:bool=False)->dict:
    state=_state(d)
    if not state.get('master_report'):raise AudioQCError('Mandatory master QC missing')
    tr=_read(d/'transcript_timeline.json');plan=_read(d/'sound_cues.json');cfg=s.audio;offset=0
    if short:
        meta=_read(d/'tiktok.json');offset=meta['source_start'];duration=meta['duration_sec']
        cfg=cfg.model_copy(update={'target_lufs':meta['target_lufs']})
        chunks=[{**c,'start':max(0,c['start']-offset),'end':min(duration,c['end']-offset)} for c in tr['chunks'] if c['end']>offset and c['start']<offset+duration]
        tr={**tr,'duration_sec':duration,'chunks':chunks}
        cues=[{**c,'start_ms':max(0,round(c['start_ms']-offset*1000)),
               'duration_ms':min(c['duration_ms'],round(duration*1000))} for c in plan['cues'] if c['start_ms']/1000<offset+duration and (c['start_ms']+c['duration_ms'])/1000>offset]
    else:
        duration=_read(d/'timeline.json')['duration_sec'];cues=plan['cues']
    root=d/'audio_engine/qc'/('encoded_tiktok' if short else 'encoded_final')
    dependencies=[dependency(Path(state['master_report']),'report')]
    if short and meta.get('short_master_report'):
        dependencies.append(dependency(Path(meta['short_master_report']),'report'))
    return analyze(path,cfg,Context(role='encoded',expected_duration=duration,timeline=tr,cues=cues,
                   dependencies=dependencies),root)


def assert_publishable(ep:int,s:Settings,path:Path,*,short=False,episode_dir:Path|None=None)->dict:
    d=directory(ep,s,episode_dir);root=d/'audio_engine/qc'/('encoded_tiktok' if short else 'encoded_final')
    state=_state(d)
    for key in ('source_report','narration_report','premaster_report','master_report'):
        if not state.get(key):raise AudioQCError('Mandatory audio stage report missing: '+key)
        assert_report(state[key])
    return assert_report(root/'audio_qc_report.json',path)


def attribution_text(ep:int,s:Settings)->str:
    state=_state(directory(ep,s))
    if not state.get('mix'):return ''
    values=_read(state['mix']).get('attributions',[])
    return '\n\nÂm thanh / attribution:\n'+'\n'.join(values) if values else ''
