import json
import httpx
import pytest
from studio.horror_story import StorySettings,schedule,validate,generate
from studio.tts.text import spoken_text,number
from studio.visual.scene_planner import accelerate_scenes
from studio.jobs import next_stage


def test_twenty_minute_beats_scale_to_real_narration():
    cfg=StorySettings()
    plan=schedule(cfg,600)
    assert plan[0]=={'beat':'cold_open','start_sec':0,'end_sec':17.5}
    assert plan[-1]['end_sec']==600
    assert all(a['end_sec']==b['start_sec'] for a,b in zip(plan,plan[1:]))
    assert schedule(StorySettings(target_duration_sec=900))[-1]['end_sec']==900


def test_duration_shortfall_is_reported_never_padded_and_continuity_checked():
    script={'sections':[{'paragraphs':[{'text':'Tôi đã chết. Nhưng tôi vẫn nghe tiếng gọi từ dưới giếng.'}]}], 'continuity':{'grandmother_status':'dead'}}
    r=validate(script,StorySettings(),{'duration_sec':20},{'facts':{'grandmother_status':'alive'}})
    assert r['status']=='FAIL' and any(i['type']=='continuity_conflict' for i in r['issues'])
    assert r['schedule'][-1]['end_sec']==20


def test_repetitive_prose_fails():
    script={'sections':[{'paragraphs':[{'text':'Một đoạn giống nhau.'}]*4} ]}
    assert validate(script,StorySettings())['status']=='FAIL'


def test_generation_uses_only_mocked_local_llm_and_selects_hook():
    draft={'title':'Làng Vọng Âm','hook_candidates':['Tôi đi về làng.','Bà tôi đã chết.','Đừng đáp giọng nói dưới giếng.'],
           'sections':[{'title':'Mở đầu','paragraphs':['Đêm mưa, tôi trở về.']}], 'continuity':{'place':'Vọng Âm'}}
    def handler(req):
        assert req.url.host=='127.0.0.1'
        assert json.loads(req.content)['stream'] is False
        return httpx.Response(200,json={'response':json.dumps(draft,ensure_ascii=False)})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        r=generate({'title':'Test'},StorySettings(),client=client)
    assert r['paid_api'] is False
    assert r['script']['sections'][0]['paragraphs'][0]['text']==r['selected_hook']['text']
    with pytest.raises(ValueError):StorySettings(local_endpoint='https://paid.example/api')


def test_speech_normalization_preserves_display_text_and_flags_unknown_codes():
    text='13/08/2008. Ngừng thở lúc 23:47. MINH2008LASTBREATH'
    normalized,issues=spoken_text(text)
    assert 'ngày mười ba tháng tám năm hai nghìn không trăm linh tám' in normalized
    assert 'hai mươi ba giờ bốn mươi bảy phút' in normalized
    assert issues==['MINH2008LASTBREATH']
    corrected,issues=spoken_text(text,{'MINH2008LASTBREATH':'Minh, hơi thở cuối cùng'})
    assert not issues and text.endswith('MINH2008LASTBREATH') and 'hơi thở cuối cùng' in corrected


def test_fast_visual_beats_still_cover_measured_narration():
    scenes=[{'scene_no':1,'start':0,'end':120,'intensity':2,'duration':120},{'scene_no':2,'start':120,'end':240,'intensity':4,'duration':120}]
    tr={'duration_sec':240,'chunks':[{'start':i} for i in range(0,240,10)]}
    result=accelerate_scenes(scenes,tr,35)
    assert len(result)>2 and result[-1]['end']==240
    assert all(a['end']==b['start'] for a,b in zip(result,result[1:]))
    assert next_stage('tts')=='tts_qc' and next_stage('master_audio_qc')=='visual_plan'


def test_approved_canon_persists_and_requires_explicit_retcon(tmp_path):
    from studio.horror_story import commit_continuity,load_continuity
    script={'title':'Series — Tập 1','story_title':'Series — Tập 1','episode_id':1,'sections':[{'paragraphs':[{'text':'Bà đã chết.'}]}],'continuity':{'grandmother':'dead'}}
    cfg=StorySettings();commit_continuity(script,tmp_path,cfg)
    next_script={**script,'story_title':'Series — Tập 2','episode_id':2,'continuity':{'grandmother':'alive'}}
    assert load_continuity(next_script,tmp_path)['facts']['grandmother']=='dead'
    with pytest.raises(ValueError,match='Continuity'):commit_continuity(next_script,tmp_path,cfg)
    next_script['continuity_updates']=['grandmother'];commit_continuity(next_script,tmp_path,cfg)
    assert load_continuity(next_script,tmp_path)['facts']['grandmother']=='alive'


def test_n8n_audio_addition_preserves_existing_webhook_contracts():
    from pathlib import Path
    workflows=Path(__file__).resolve().parents[1]/'n8n/workflows'
    wf=json.loads((workflows/'WF-08_audio_production_qc.json').read_text())
    assert wf['active'] is False
    webhook=next(n for n in wf['nodes'] if n['type']=='n8n-nodes-base.webhook')
    assert webhook['parameters']['path']=='ass/wf08-audio' and webhook['parameters']['authentication']=='headerAuth'
    assert any('stages/tts_qc' in n['parameters'].get('url','') for n in wf['nodes'])
    for old in ['WF-02_script_builder.json','WF-03_tts_production.json','WF-07_render_qc_publishing.json']:
        assert (workflows/old).is_file()


def test_local_model_invalid_json_shape_is_rejected():
    bad={'title':'Missing scenes','hook_candidates':['one','one','one'],'sections':[]}
    def handler(req):return httpx.Response(200,json={'response':json.dumps(bad)})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):generate({'title':'Test'},StorySettings(),client=client)
