"""Developer commands; offline episode builds are isolated and do not enqueue/publish anything."""
from pathlib import Path
import json,shutil
from .analyzer import analyze,Context
from .repair import repair
from .mastering import master
from .gate import assert_report,record_review,AudioQCError,ListeningReviewRequired
from .io import identity,dump,stable_hash
from . import production
from . import ENGINE_VERSION


def _context(args):
    timeline=json.loads(Path(args.timeline).read_text()) if getattr(args,'timeline',None) else None
    return Context(role=getattr(args,'role','source'),expected_duration=getattr(args,'duration',None),timeline=timeline,
                   require_listening_review=getattr(args,'require_listening_review',False))

def cmd_audio(args):
    from ..config import get_settings
    s=get_settings();cfg=s.audio;op=args.operation
    if op=='review':
        out=record_review(args.report,args.decision,args.reviewer,args.note);print(out);return
    if op=='mix':
        working=Path(args.episode_dir) if args.episode_dir else None
        result=production.prepare_episode(args.episode,s,working)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        try:
            assert_report(production._state(production.directory(args.episode,s,working))['master_report'])
        except ListeningReviewRequired:
            raise SystemExit(3)
        return
    path=Path(args.input).resolve();out=Path(args.out) if args.out else s.storage/'logs/audio'/stable_hash([str(path),identity(path)['sha256'],op])[:16]
    if op=='repair':result=repair(path,out,cfg,_context(args))
    elif op=='master':result=master(path,out,cfg)
    else:result=analyze(path,cfg,_context(args),out)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if result.get('status')=='FAIL' or result.get('after_status')=='FAIL':raise SystemExit(2)
    if result.get('status')=='NEEDS_LISTENING_REVIEW' or result.get('after_status')=='NEEDS_LISTENING_REVIEW':raise SystemExit(3)


def cmd_episode(args):
    from ..config import get_settings
    from .. import render,qc,shorts
    s=get_settings();ep=args.episode;original=s.storage/'episodes'/str(ep)
    required=['episode_script.json','transcript_timeline.json','timeline.json','scene_plan.json','subtitles.srt']
    transcript=json.loads((original/'transcript_timeline.json').read_text())
    source_files={transcript.get('source_audio_path') or transcript['audio_path']}
    source_files.update(c['audio_path'] for c in transcript['chunks'] if c.get('audio_path'))
    key=stable_hash({'files':[identity(original/n) for n in required],
                     'sources':[identity(p) for p in sorted(source_files) if Path(p).is_file()],
                     'audio':s.audio.model_dump(),'story':s.story_engine.model_dump(),'revision':ENGINE_VERSION})[:16]
    d=original if args.in_place else original/'audio_upgrade'/key/'episode'
    output=s.storage/'output'/str(ep)/'audio_upgrade'/key;output.mkdir(parents=True,exist_ok=True)
    if d!=original:
        d.mkdir(parents=True,exist_ok=True)
        for name in required+['scene_assets_map.json']:
            if (original/name).exists() and not (d/name).exists():shutil.copy2(original/name,d/name)
        if not (d/'clips').exists():(d/'clips').symlink_to((original/'clips').resolve(),target_is_directory=True)
    print('Isolated working directory: '+str(d),flush=True)
    results=production.prepare_episode(ep,s,d)
    print('Audio stems, master and QC created.',flush=True)
    height=args.height or s.final_height;video=output/'final_video.mp4'
    render.render_episode(ep,height=height,out_path=video,crf=args.crf,settings=s,episode_dir=d)
    report=qc.check_video(ep,video,expect_height=height,settings=s,episode_dir=d)
    result={'episode_id':ep,'directory':str(d),'output':str(output),'video':str(video),'video_qc':report,'stages':results,'deployed':False,'published':False}
    if args.short:
        short=shorts.render_short(ep,s,episode_dir=d,out_path=output/'tiktok_1080x1920.mp4')
        result['short']=short;result['short_qc']=qc.check_short(ep,s,episode_dir=d)
        try:
            production.assert_publishable(ep,s,Path(short['path']),short=True,episode_dir=d)
            result['short_publish_gate']='PASS'
        except ListeningReviewRequired:result['short_publish_gate']='NEEDS_LISTENING_REVIEW'
        except AudioQCError:result['short_publish_gate']='FAIL'
    try:
        production.assert_publishable(ep,s,video,episode_dir=d)
        result['publish_gate']='PASS'
    except ListeningReviewRequired as exc:result['publish_gate']='NEEDS_LISTENING_REVIEW';result['reason']=str(exc)
    except AudioQCError as exc:result['publish_gate']='FAIL';result['reason']=str(exc)
    dump(output/'run_report.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    if not report['passed'] or result['publish_gate']=='FAIL' or (args.short and (not result['short_qc']['passed'] or result['short_publish_gate']=='FAIL')):raise SystemExit(2)
    if result['publish_gate']=='NEEDS_LISTENING_REVIEW' or result.get('short_publish_gate')=='NEEDS_LISTENING_REVIEW':raise SystemExit(3)


def cmd_story(args):
    from ..config import get_settings
    from ..horror_story import generate,schedule
    cfg=get_settings().story_engine;brief=json.loads(Path(args.brief).read_text())
    continuity=json.loads(Path(args.continuity).read_text()) if args.continuity else {}
    if args.outline_only:
        result={'brief':brief,'schedule':schedule(cfg),'generation':'outline-only; narration has not been authored','paid_api':False}
    else:result=generate(brief,cfg,continuity)
    out=Path(args.out);dump(out,result)
    if 'script' in result:
        from ..horror_story import export_draft
        export_draft(result,out,rights=brief.get('rights','unknown'))
    print(out)


def register(sub):
    p=sub.add_parser('audio',help='local DSP, mastering, diagnostics and file-bound listening reviews')
    ops=p.add_subparsers(dest='operation',required=True)
    for name in ('analyze','repair','master','qc'):
        command=ops.add_parser(name);command.add_argument('input');command.add_argument('--out')
        command.add_argument('--role',choices=['source','narration','premaster','master','encoded'],default='encoded' if name=='qc' else 'source')
        command.add_argument('--duration',type=float);command.add_argument('--timeline');command.add_argument('--require-listening-review',action='store_true')
        command.set_defaults(fn=cmd_audio)
    command=ops.add_parser('mix');command.add_argument('episode',type=int);command.add_argument('--episode-dir');command.set_defaults(fn=cmd_audio)
    command=ops.add_parser('review');command.add_argument('report');command.add_argument('--decision',choices=['accepted','rejected'],required=True)
    command.add_argument('--reviewer',required=True);command.add_argument('--note',required=True);command.set_defaults(fn=cmd_audio)
    p=sub.add_parser('episode');ops=p.add_subparsers(dest='operation',required=True);command=ops.add_parser('render')
    command.add_argument('episode',type=int);command.add_argument('--height',type=int);command.add_argument('--crf',type=int,default=20)
    command.add_argument('--short',action='store_true');command.add_argument('--in-place',action='store_true',help='explicitly use canonical episode metadata; default is isolated')
    command.set_defaults(fn=cmd_episode)
    p=sub.add_parser('story');ops=p.add_subparsers(dest='operation',required=True);command=ops.add_parser('generate')
    command.add_argument('--brief',required=True);command.add_argument('--out',required=True);command.add_argument('--continuity');command.add_argument('--outline-only',action='store_true');command.set_defaults(fn=cmd_story)
