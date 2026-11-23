"""DSP calibration and safety contracts using only deterministic original synthetic signals."""
import json
from pathlib import Path
import numpy as np
import pytest
from studio.audio_engine.settings import AudioSettings
from studio.audio_engine.io import write,digest,decode
from studio.audio_engine.analyzer import analyze,Context,flag,write_reports
from studio.audio_engine.repair import repair
from studio.audio_engine.gate import assert_report,record_review,AudioQCError,ListeningReviewRequired,dependency
from studio.audio_engine.assets import Catalog,procedural
from studio.audio_engine.soundscape import plan,SoundCue
from studio.audio_engine.mixing import mix,envelope
from studio.audio_engine.mastering import master
from studio import media

RATE=48000
@pytest.fixture
def cfg():return AudioSettings(export_review_clips=False)
def clean(seconds=4):
    t=np.arange(round(seconds*RATE))/RATE
    a=(.06+.035*np.sin(2*np.pi*.7*t))*np.sin(2*np.pi*220*t)+.015*np.sin(2*np.pi*443*t)
    a[:480]*=np.linspace(0,1,480);a[-480:]*=np.linspace(1,0,480)
    return a.astype('float32')
def wav(tmp_path,a,name='source.wav'):return write(tmp_path/name,a,RATE)
def kinds(r):return {f['defect_type'] for f in r['flags']}

def test_clean_narration_not_destroyed(tmp_path,cfg):
    p=wav(tmp_path,clean());before=digest(p);r=analyze(p,cfg)
    assert r['status']=='PASS',r['flags']
    fixed=repair(p,tmp_path/'repairs',cfg)
    assert fixed['output']==str(p.resolve()) and fixed['attempts']==[] and digest(p)==before

def test_hard_clipped_source_requires_regeneration_even_if_gain_reduced(tmp_path,cfg):
    a=np.clip(clean()*40,-1,1);r=analyze(wav(tmp_path,a),cfg)
    assert r['status']=='FAIL' and 'source_clipping' in kinds(r)
    quiet=analyze(wav(tmp_path,a*.3,'quiet.wav'),cfg)
    assert 'saturation_candidate' in kinds(quiet) and quiet['status']=='NEEDS_LISTENING_REVIEW'
    fixed=repair(tmp_path/'source.wav',tmp_path/'repairs',cfg)
    assert fixed['after_status']=='FAIL' and not fixed['attempts']

def test_near_clipping_is_not_proof_of_distortion(tmp_path,cfg):
    a=clean();a*=.9992/abs(a).max();r=analyze(wav(tmp_path,a),cfg)
    assert 'near_clipping' in kinds(r) and 'source_clipping' not in kinds(r)
    assert r['status']=='WARNING'

def test_isolated_pop_repair_is_local_idempotent_and_preserves_source(tmp_path,cfg):
    a=clean();a[2*RATE]=.95;p=wav(tmp_path,a);original=digest(p);r=analyze(p,cfg)
    assert 'click_candidate' in kinds(r),r['flags']
    fixed=repair(p,tmp_path/'repairs',cfg)
    assert fixed['attempts'] and len(fixed['attempts'])<=2
    assert fixed['after_status']=='PASS' and digest(p)==original
    assert repair(p,tmp_path/'repairs',cfg)==fixed
    b,_=decode(fixed['output']);raw,_=decode(p)
    assert np.count_nonzero(np.abs(b-raw)>1e-5)<=3

def test_stationary_hiss_and_hum_are_uncertain_not_blindly_removed(tmp_path,cfg):
    rng=np.random.default_rng(7);a=.02*rng.normal(size=RATE*4)+clean()*.1
    r=analyze(wav(tmp_path,a,'hiss.wav'),cfg)
    assert 'stationary_noise_candidate' in kinds(r)
    repaired=repair(tmp_path/'hiss.wav',tmp_path/'repairs',cfg)
    assert repaired['attempts']==[]
    t=np.arange(RATE*4)/RATE;a=.06*np.sin(2*np.pi*60*t)+clean()*.1
    r=analyze(wav(tmp_path,a,'hum.wav'),cfg)
    assert 'hum_candidate' in kinds(r)

def test_unexpected_silence_vs_authored_suspense_pause(tmp_path,cfg):
    a=clean(8);a[2*RATE:6*RATE]=0;p=wav(tmp_path,a)
    assert 'unexpected_silence' in kinds(analyze(p,cfg))
    r=analyze(p,cfg,Context(intentional_silences=[(2,6)]))
    assert 'unexpected_silence' not in kinds(r)
    # Background-only silence must never excuse missing words in a narration chunk.
    tl={'duration_sec':8,'chunks':[{'start':0,'end':8,'text':'expected voice'}]}
    cues=[{'category':'sudden_silence','silence_scope':'background','start_ms':2000,'duration_ms':4000}]
    assert 'missing_narration' in kinds(analyze(p,cfg,Context(timeline=tl,cues=cues)))

def test_missing_chunk_overlap_and_truncated_ending_fail(tmp_path,cfg):
    p=wav(tmp_path,clean());tl={'duration_sec':5,'chunks':[{'start':0,'end':3,'audio_path':str(tmp_path/'missing.wav')},{'start':2,'end':5}]}
    r=analyze(p,cfg,Context(expected_duration=5,timeline=tl))
    assert {'missing_tts_chunk','narration_timeline','duration_mismatch'}<=kinds(r)

def test_boundary_click_and_intentional_transient_protection(tmp_path,cfg):
    a=clean();a[2*RATE]=.95;p=wav(tmp_path,a)
    cue={'stem':'sfx','start_ms':1950,'duration_ms':300}
    assert 'click_candidate' not in kinds(analyze(p,cfg,Context(role='premaster',cues=[cue])))
    assert 'click_candidate' in kinds(analyze(p,cfg,Context(role='source',cues=[cue])))
    a=clean();a[2*RATE:]=.1;p=wav(tmp_path,a,'splice.wav')
    assert 'splice_click' in kinds(analyze(p,cfg,Context(boundaries=[2])))

@pytest.mark.parametrize('name',['wooden_door_knock','distant_scream','eerie_stinger'])
def test_horror_transients_not_treated_as_certain_defects(tmp_path,cfg,name):
    a=procedural(name,RATE);p=wav(tmp_path,a,name+'.wav')
    r=analyze(p,cfg,Context(role='premaster',cues=[{'stem':'sfx','start_ms':0,'duration_ms':4000}]))
    assert r['status']!='FAIL' and 'click_candidate' not in kinds(r)

def test_gate_requires_file_bound_review_and_rejects_tampering(tmp_path,cfg):
    p=wav(tmp_path,clean());root=tmp_path/'qc';analyze(p,cfg,Context(require_listening_review=True),root)
    report=root/'audio_qc_report.json'
    with pytest.raises(ListeningReviewRequired):assert_report(report,p)
    record_review(report,'accepted','test listener','Synthetic fixture checked for the intended review behavior')
    assert_report(report,p)
    write(p,clean()*.5,RATE)
    with pytest.raises(AudioQCError,match='changed'):assert_report(report,p)

def test_gate_blocks_missing_stale_and_failed_dependency(tmp_path,cfg):
    p=wav(tmp_path,clean());root=tmp_path/'qc';analyze(p,cfg,out_dir=root)
    parent=tmp_path/'parent';analyze(p,cfg,Context(dependencies=[dependency(root/'audio_qc_report.json','report')]),parent)
    assert_report(parent/'audio_qc_report.json',p)
    (root/'audio_qc_report.json').write_text('{}')
    with pytest.raises(AudioQCError,match='dependency'):assert_report(parent/'audio_qc_report.json',p)
    with pytest.raises(AudioQCError):assert_report(tmp_path/'missing.json',p)

def test_asset_license_checksum_cache_and_determinism(tmp_path,cfg):
    p=wav(tmp_path,clean());catalog=tmp_path/'catalog.json'
    catalog.write_text(json.dumps({'assets':{'foo':{'path':str(p),'license':'unknown','sha256':digest(p)}}}))
    cfg=cfg.model_copy(update={'asset_catalog':str(catalog)})
    with pytest.raises(ValueError,match='license'):Catalog(tmp_path,cfg,tmp_path).resolve('foo')
    catalog.write_text(json.dumps({'assets':{'foo':{'path':str(p),'license':'CC-BY-4.0','sha256':digest(p)}}}))
    with pytest.raises(ValueError,match='Attribution'):Catalog(tmp_path,cfg,tmp_path).resolve('foo')
    c=Catalog(tmp_path,cfg,tmp_path);a=c.resolve('heartbeat');b=c.resolve('heartbeat')
    assert a['sha256']==b['sha256'] and a['license']=='CC0-1.0' and a['fallback']

def test_cues_real_timing_and_no_audible_thirteenth_bell():
    tr={'duration_sec':10,'chunks':[{'start':0,'end':10,'text':'Tiếng chuông thứ mười ba không tồn tại trong không khí.'}]}
    r=plan({},tr,[{'scene_no':1,'start':0,'end':10}])
    assert not any(c['asset_id']=='temple_bell' for c in r['cues'])
    for c in r['cues']:
        SoundCue.model_validate(c);assert c['start_ms']+c['duration_ms']<=10000

def test_ducking_follows_speech_not_a_fixed_gain(cfg):
    a=np.zeros((RATE*6,2),dtype='float32');a[RATE:3*RATE]=clean(2)[:,None]
    duck,rms,active=envelope(a,RATE,cfg,[])
    assert duck[0]>.99 and duck[100]<.5 and duck[-1]>.8

def test_mix_detects_masking_and_preserves_five_stems(tmp_path,cfg):
    p=wav(tmp_path,clean());asset=wav(tmp_path,clean()*8,'loud.wav')
    cue=SoundCue(scene_id=1,cue_id='a',asset_id='loud',category='sfx',stem='sfx',trigger='test',relative_position=0,
                 start_ms=0,duration_ms=4000,gain_db=0,fade_in_ms=0,fade_out_ms=0,priority=1,emotional_intensity=4).model_dump()
    r=mix(p,{'duration_ms':4000,'cues':[cue],'tension_curve':[]},{'loud':{'path':str(asset),'attribution':''}},tmp_path/'mix',cfg)
    assert set(r['outputs'])=={'narration','ambience','music','sfx','audio_signature','premaster'}
    assert any(f['defect_type']=='narration_masking' for f in r['flags'])

def test_two_pass_master_and_actual_aac_qc(tmp_path,cfg):
    p=wav(tmp_path,clean(6));r=master(p,tmp_path/'masters',cfg)
    assert r['two_pass'] and abs(r['after_metrics']['integrated_lufs']+16)<=1
    assert r['after_metrics']['true_peak_dbtp']<=-1.5
    assert r['after_metrics']['bit_depth']==24 and r['after_metrics']['channels']==2
    aac=tmp_path/'encoded.m4a';media.ffmpeg(['-i',r['output']['path'],'-c:a','aac','-b:a','256k',str(aac)])
    q=analyze(aac,cfg,Context(role='encoded',expected_duration=6))
    assert q['status'] in ('PASS','WARNING'),q['flags']
    assert q['metrics']['codec']=='aac'

def test_actual_av_offset_fails(tmp_path,cfg):
    p=wav(tmp_path,clean(4));v=tmp_path/'bad.mp4'
    media.ffmpeg(['-f','lavfi','-i','color=black:s=64x64:d=4:r=25','-itsoffset','0.3','-i',str(p),'-c:v','libx264','-c:a','aac','-b:a','256k',str(v)])
    r=analyze(v,cfg,Context(role='encoded'))
    assert 'av_sync' in kinds(r)

def test_decode_failure_produces_diagnostics(tmp_path,cfg):
    p=tmp_path/'bad.wav';p.write_bytes(b'broken')
    r=analyze(p,cfg,out_dir=tmp_path/'qc')
    assert r['status']=='FAIL' and not r['metrics']['decode']
    assert (tmp_path/'qc/audio_qc_report.md').is_file()


def test_repairs_stop_when_candidate_measures_worse(tmp_path,cfg,monkeypatch):
    import importlib
    module=importlib.import_module('studio.audio_engine.repair')
    original=module.analyze
    a=clean();a[RATE*2]=.95;p=wav(tmp_path,a);before=digest(p)
    def worse(path,*args,**kwargs):
        r=original(path,*args,**kwargs)
        if Path(path).name.startswith('attempt-'):
            r['flags'].append(flag('synthetic_quality_regression',0,1,'critical',1,{},'reject repair'))
            r['status']='FAIL'
        return r
    monkeypatch.setattr(module,'analyze',worse)
    r=module.repair(p,tmp_path/'repairs',cfg)
    assert len(r['attempts'])==1 and r['attempts'][0]['quality_worsened']
    assert r['output']==str(p.resolve()) and digest(p)==before


def test_critical_source_cannot_be_listening_approved(tmp_path,cfg):
    p=wav(tmp_path,np.clip(clean()*40,-1,1));root=tmp_path/'qc';analyze(p,cfg,out_dir=root)
    with pytest.raises(AudioQCError,match='cannot override'):
        record_review(root/'audio_qc_report.json','accepted','listener','Cannot accept clipped source')


def test_entire_short_chunk_dropout_is_not_excused_by_global_silence_limit(tmp_path,cfg):
    a=clean();a[RATE:RATE*2]=0
    tl={'duration_sec':4,'chunks':[{'chunk_no':1,'start':0,'end':.9},{'chunk_no':2,'start':1,'end':2},{'chunk_no':3,'start':2.1,'end':4}]}
    assert 'missing_narration' in kinds(analyze(wav(tmp_path,a),cfg,Context(timeline=tl)))


def test_mandatory_master_bit_depth_and_bad_encode(tmp_path,cfg):
    p=wav(tmp_path,clean());f=tmp_path/'bad16.wav';media.ffmpeg(['-i',str(p),'-c:a','pcm_s16le',str(f)])
    assert 'master_bit_depth' in kinds(analyze(f,cfg,Context(role='master')))
    assert 'encoded_codec' in kinds(analyze(p,cfg,Context(role='encoded')))


def test_channel_imbalance_and_antiphase_are_review_candidates(tmp_path,cfg):
    a=clean();p=wav(tmp_path,np.column_stack([a,-a*.1]))
    r=analyze(p,cfg,Context(role='premaster'))
    assert {'channel_imbalance','phase_candidate'}<=kinds(r)


def test_cached_asset_tampering_is_rejected(tmp_path,cfg):
    p=wav(tmp_path,clean());entry={'path':str(p),'license':'original','source':'test synth','sha256':digest(p),'attribution':''}
    catalog=tmp_path/'catalog.json';catalog.write_text(json.dumps({'assets':{'clean':entry}}))
    cfg=cfg.model_copy(update={'asset_catalog':str(catalog)})
    c=Catalog(tmp_path,cfg,tmp_path);asset=c.resolve('clean');Path(asset['path']).write_bytes(b'altered')
    with pytest.raises(ValueError,match='Cached'):c.resolve('clean')


def test_repair_attempts_and_gain_settings_are_bounded():
    with pytest.raises(ValueError):AudioSettings(max_auto_repair_attempts=3)
    with pytest.raises(ValueError):AudioSettings(duck_min_db=14,duck_max_db=8)


def test_report_boolean_cannot_hide_unresolved_review(tmp_path,cfg):
    from studio.audio_engine.io import dump
    p=wav(tmp_path,clean());root=tmp_path/'qc';r=analyze(p,cfg,Context(require_listening_review=True),root)
    r['status']='PASS';r['passed']=True;dump(root/'audio_qc_report.json',r)
    with pytest.raises(ListeningReviewRequired):assert_report(root/'audio_qc_report.json',p)


def test_persistent_unexplained_level_step_requires_review(tmp_path,cfg):
    a=clean(6);a[:3*RATE]*=.05;p=wav(tmp_path,a)
    r=analyze(p,cfg)
    assert 'unexplained_level_jump' in kinds(r)
    # A declared speaker boundary is not silently "fixed" as defective gain.
    assert 'unexplained_level_jump' not in kinds(analyze(p,cfg,Context(boundaries=[3])))


def test_qc_version_change_invalidates_old_evidence(tmp_path,cfg):
    from studio.audio_engine.io import dump
    p=wav(tmp_path,clean());root=tmp_path/'qc';r=analyze(p,cfg,out_dir=root)
    r['engine_version']='obsolete';dump(root/'audio_qc_report.json',r)
    with pytest.raises(AudioQCError,match='version'):assert_report(root/'audio_qc_report.json',p)
