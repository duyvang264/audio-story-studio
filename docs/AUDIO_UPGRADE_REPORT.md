# Horror Audio Engine implementation report

Implemented incrementally in Audio Story Studio on 2026-11-23, engine version 1.0.0. No production deployment, service restart, workflow activation or publishing was performed. Original recordings and existing canonical videos were preserved. No paid API or paid asset generation was invoked.

## Audit and architecture

The existing Python/FastAPI application, PostgreSQL job queue, Telegram commands, n8n workflows, TTS adapters, FFmpeg renderer and Visual Story Engine were retained. The audit found normalization embedded in rendering, incomplete source/final-encode quality evidence, no mandatory audio publication gate, missing sound catalog/provenance, and imported story metadata being discarded. The implementation adds small modules and stages to the existing worker rather than introducing a service.

The resulting sequence is script validation → scene analysis → TTS → source QC → conservative repair → narration QC → licensed sound resolution → timed cues → stem mix → two-pass master → master QC → existing visual/render stages → actual encoded-file QC. Human content and publishing approval remain separate. Original stage/API/webhook contracts remain compatible; the new n8n workflow is inactive.

## Implemented behavior

- Configurable default 20-minute structure, three candidate hooks, mini-hooks, climax/cliffhanger, duration validation and explicit series canon. Actual narration duration drives timing. Optional local-only LLM generation is available through CLI, queued API and Telegram; it does not approve rights or start TTS automatically.
- Optional offline VieNeu batch adapter with voice cast, pronunciation substitutions, native floating-point source preservation, immutable chunk caches and explicit targeted retakes. No paid fallback.
- Scene-derived tension, 87 timed cues in the real trial, catalog checksums/license/attribution and 22 deterministic local fallback assets. Procedural sounds are stylized approximations, not recorded village ambience or authentic human whispers.
- Conditional narration processing. Clean narration passes through; no automatic aggressive denoising, compression or de-essing. Source clipping is not represented as repaired by normalization. High-confidence local clicks/DC/splices can be repaired separately, compared and reanalyzed, with at most two attempts and rejection on worsening.
- Five stems, floating-point premaster, speech-priority adaptive ducking, masking checks, short transition fades and two-pass EBU R128 mastering. Defaults: 48 kHz stereo PCM24 WAV, AAC 256k, −16 ±1 LUFS and true peak ≤−1.5 dBTP. Existing short-form −14 LUFS target is preserved separately.
- Source, narration, premaster, master and actual encoded-file QC. Reports include measurements, confidence, flagged intervals, proposed actions and short listening clips. Checks cover decoding, clipping/plateaus, click candidates, noise/hum/rumble, silence/dropouts, source coverage, format, level jumps, channel/phase problems and A/V timing.
- Publication checks SHA-256 identities recursively across reports and dependencies, including engine version. Stale evidence, missing stages, critical failures and unresolved listening review block publication. Human review cannot override critical defects.
- Isolated episode renders, bounded job retries, cached/idempotent execution, authenticated report/review endpoints and retained diagnostic artifacts.

## Files changed

New engine modules: `studio/audio_engine/{__init__,settings,io,analyzer,gate,repair,assets,soundscape,mixing,mastering,production,cli}.py`.

New story/TTS support: `studio/horror_story.py`, `studio/tts/text.py`, `studio/tts/vieneu_worker.py`; catalog: `config/sound_assets.json`.

Existing integration points updated: `studio/{config,audio,budget,story,jobs,pipeline,timeline,render,shorts,qc,media,cli,api,commands}.py`, `studio/tts/{base,__init__}.py` where applicable, and `studio/visual/{scene_planner,engine}.py`. Dependency/package configuration is in `pyproject.toml`; environment examples are in `.env.example`.

Automation/documentation: `scripts/gen_n8n_workflows.py`, regenerated workflow JSON including inactive `n8n/workflows/WF-08_audio_production_qc.json`, `README.md`, `docs/AUDIO_UPGRADE_PLAN.md`, `docs/AUDIO_ENGINE.md` and this report. Tests include new audio/story suites and existing auth, approval, API and database/media integration suites.

## Real episode measurements

Used existing episode 8, “Ngôi Làng Không Có Người Chết — Tập 01”, with 106 source performance chunks. The authored narration is about 11:07; it was not stretched or padded to 20 minutes. Existing 12 images/22 timed shots and subtitles were reused. All figures below are measured from actual encoded files, not inferred from mastering settings.

| Measurement | Existing long video | New long video | New short |
|---|---:|---:|---:|
| Audio duration | 666.791 s | 666.791 s | 85.840 s |
| Integrated loudness | −16.42 LUFS | −16.01 LUFS | −13.99 LUFS |
| True peak | −1.15 dBTP | −3.87 dBTP | −2.09 dBTP |
| Clipped sample ratio | 0 | 0 | 0 |
| Channels / codec | mono AAC | stereo AAC | stereo AAC |
| A/V start offset | 0 ms | 0 ms | 0 ms |
| A/V duration difference | 9.396 ms | 9 ms | 0 ms |

The original exceeds the configured true-peak ceiling. New encoded outputs pass numerical QC. The PCM master measures −16.0 LUFS, −3.93 dBTP and LRA 2.2 LU; LRA 7 is an editorial target, not a reason to force artificial dynamics. The narration repair stage made zero repair attempts because its analyzed assembled input passed; uncertain source hum/rumble and perceptual review remain unresolved. Therefore **both publication gates correctly remain NEEDS_LISTENING_REVIEW**. This is not proof that the previous rè or incorrect words have been fixed. Expected isolated CLI exit code is 3 until listening review is resolved.

Artifacts, relative to the repository:

- `storage/output/8/audio_upgrade/007dc8e3b78b6081/final_audio.wav` — PCM24 master, hash verified against production master.
- `storage/output/8/audio_upgrade/007dc8e3b78b6081/final_audio_review.mp3` — convenient listening copy.
- `storage/output/8/audio_upgrade/007dc8e3b78b6081/final_video.mp4` — 1920×1080 trial.
- `storage/output/8/audio_upgrade/007dc8e3b78b6081/tiktok_1080x1920.mp4` — short trial.
- Same directory: `run_report.json`, `before_after_measurements.json`.
- `storage/episodes/8/audio_upgrade/007dc8e3b78b6081/episode/audio_engine/` — cues, resolved assets, state and stage QC; source listening reports/clips are under `qc/source/`.

## Verification and remaining limitations

Final full-suite command: `.venv/bin/python -m pytest -q` against the separate test database. Result: **138 passed, 1 warning in 22.87 seconds**. The warning is the Starlette TestClient/httpx deprecation notice.

Automated coverage includes clean narration, hard/near/reduced-gain clipping, isolated pops, splice clicks, hiss, hum, saturation candidates, missing/short silent chunks, intentional silence, source overlap/truncation, masking, ducking, knock/scream/stinger false positives, safe/idempotent repair, rejection of worsening repairs, stale/missing/tampered review evidence, phase/channel balance, actual AAC encoding and A/V mismatch. Integration coverage exercises approval/upload blocking, authenticated endpoints, preserved imports, targeted takes, idempotent jobs and unchanged workflow contracts. Test signals are synthetic and HTTP/LLM generation is mocked; no test credits were spent. The real episode render and optional offline two-chunk VieNeu smoke test also ran successfully, with listening review left pending.

No local LLM runtime was installed on this machine, so new local story generation was schema/HTTP/mock tested rather than claimed as a completed generated episode. Correct Vietnamese words, believable acting and all forms of audible distortion cannot be certified by numeric heuristics or ASR. Thresholds need continued calibration against reviewed real voices. Procedural assets are a legal local fallback, not a replacement for a curated recorded sound library. Review uncertain segments before approving; regenerate defective takes rather than hiding them with heavy processing.

## Usage and cost

See [installation, configuration, n8n, repair and listening workflow](AUDIO_ENGINE.md). CLI equivalents are available under `audio analyze`, `audio repair`, `audio mix`, `audio master`, `audio qc`, `audio review`, `story generate` and `episode render`:

```sh
.venv/bin/python -m studio.cli audio --help
.venv/bin/python -m studio.cli episode render 8 --short
```

The render command defaults to an isolated content-addressed output, without queueing publication. Install/restart/activate production only through the existing operator process. New engine versions invalidate old QC evidence; rerun the affected pipeline rather than editing reports.

Measured API/asset expenditure for this implementation and trial: **0 VND**. Local processing has no per-call API charge; electricity, storage, hardware and hosting are separate. The configurable monthly application budget cannot exceed 500,000 VND, and paid/unknown providers remain gated. No claim is made that external hosting or subscriptions are covered by that amount.
