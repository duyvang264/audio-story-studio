# Horror Audio Engine

This extends the existing native Python worker, PostgreSQL jobs, FastAPI endpoints, visual engine and Docker n8n installation. No service is restarted or deployed by this change. Local DSP and procedural sound generation make no paid API calls. The code preserves authored recordings and writes repairs, mixes and masters into content-addressed directories.

## Architecture

`script_build → story_validate → scene_sound_analysis → tts → tts_qc → audio_repair → narration_qc → sound_assets → sound_timeline → audio_mix → audio_master → master_audio_qc → visual_plan → visual_generate → timeline → render_preview → qc_preview → notify_preview`

The existing content approval still triggers `render_final → qc_final → render_tiktok → qc_tiktok → publish_queue`. A separate human publish approval remains necessary. Publication checks fresh evidence again immediately before upload. Existing stage names, API URLs, Telegram commands and n8n webhook contracts remain valid. New jobs use the existing retries, heartbeat and idempotency keys; QC failures are non-retryable, and each deterministic repair has at most two attempts across retries.

The modules in `studio/audio_engine/` separate I/O/provenance, analysis, repair, catalog, cue planning, mixing, mastering, orchestration and CLI. The only new mandatory Python dependency is NumPy. FFmpeg performs codec I/O and EBU R128/true-peak measurement. SciPy and a new service are unnecessary.

## Installation on Mac mini M4

Use the project's existing Python environment and FFmpeg installation:

```sh
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m studio.cli audio --help
.venv/bin/python -m studio.cli episode render --help
.venv/bin/python -m pytest -q
```

Tests use the separate `*_test` PostgreSQL database and refuse to reset another database. Run the native API/worker as before; n8n stays in Docker because the native worker needs macOS tools. Restarting production services is an operator action, not part of this implementation.

VieNeu is optional, isolated from the app's Python version. The existing local runtime at `storage/tools/vieneu/venv/bin/python` is supported; install `vieneu==3.8.5` in a Python 3.12 environment if setting up a fresh machine. Download the open-source v3 Turbo CPU model once, then the production adapter forces `HF_HUB_OFFLINE=1`. A missing runtime/model fails clearly; it never falls back to a paid API. The adapter batches a voice's requests in one process to avoid loading the model once per sentence and preserves native floating-point outputs before PCM conversion.

Example optional provider configuration:

```dotenv
TTS_PROVIDER=vieneu
VIENEU_VOICE=Thiện Minh
VIENEU_PYTHON=storage/tools/vieneu/venv/bin/python
TTS_PRONUNCIATIONS={"MINH2008LASTBREATH":"Minh, năm hai nghìn không trăm linh tám, hơi thở cuối cùng"}
ALLOW_PAID_API=false
PUBLISH_UPLOAD_ENABLED=false
```

Dates and clock times are normalized for pronunciation separately from subtitle text. Unhandled mixed uppercase/digit identifiers trigger listening review. Paragraphs may specify a `voice`, or `speaker` with a script `voice_cast` mapping. Generation and ASR do not guarantee correct pronunciation; check Vietnamese names, consonants, numbers and file identifiers by listening.

## Configuration

Existing flat variables remain compatible. New nested settings use `AUDIO__*` and `STORY_ENGINE__*`; all fields are defined and validated in `studio/audio_engine/settings.py` and `studio/horror_story.py`.

```dotenv
AUDIO__SAMPLE_RATE=48000
AUDIO__CHANNELS=2
AUDIO__AAC_BITRATE=256k
AUDIO__TARGET_LUFS=-16
AUDIO__LOUDNESS_TOLERANCE_LU=1
AUDIO__MAX_TRUE_PEAK_DBTP=-1.5
AUDIO__CODEC_HEADROOM_DB=0.75
AUDIO__TARGET_LRA_LU=7
AUDIO__SYNC_TOLERANCE_MS=100
AUDIO__MAX_AUTO_REPAIR_ATTEMPTS=2
AUDIO__CONDITIONAL_DENOISE=false
AUDIO__CONDITIONAL_DEESS=false
AUDIO__CONDITIONAL_COMPRESSION=false
AUDIO__REQUIRE_LISTENING_REVIEW=true
AUDIO__DUCK_MIN_DB=8
AUDIO__DUCK_MAX_DB=14
AUDIO__DUCK_ATTACK_MS=80
AUDIO__DUCK_RELEASE_MS=600
AUDIO__SFX_DUCK_DB=8
AUDIO__PROCEDURAL_FALLBACK=true
AUDIO__ASSET_CATALOG=config/sound_assets.json
AUDIO__FAST_VISUAL_SEC=35
STORY_ENGINE__TARGET_DURATION_SEC=1200
STORY_ENGINE__STRICT_DURATION=false
```

These loudness/LRA values are editorial defaults, not universal platform specifications. LRA is a mastering target, not a promise to force every voice's dynamics to exactly 7 LU. The long-form master is 48 kHz stereo PCM 24-bit WAV. The TikTok cut has its own two-pass master using existing `TIKTOK_LUFS` (default -14); both encoded AAC files are analyzed against their actual targets. Codec headroom is deliberate because AAC can overshoot PCM peaks. If encoded true peak fails, rerender with more clean-source headroom, never declare the PCM measurement sufficient.

Default regular visual scene rules remain 120 seconds (45–150 seconds). Cold-open, intense and final scenes can split around real TTS chunk boundaries using `AUDIO__FAST_VISUAL_SEC`. Existing explicitly curated image timelines remain usable.

## Commands

```sh
.venv/bin/python -m studio.cli audio analyze input.wav --out storage/logs/audio-check
.venv/bin/python -m studio.cli audio repair input.wav --out storage/audio/repairs
.venv/bin/python -m studio.cli audio mix 8
.venv/bin/python -m studio.cli audio master premaster.wav --out storage/audio/masters
.venv/bin/python -m studio.cli audio qc final_video.mp4 --role encoded --duration 666.8
.venv/bin/python -m studio.cli episode render 8 --short
```

`episode render` defaults to an isolated working copy under `storage/episodes/<id>/audio_upgrade/<hash>/episode` and outputs under `storage/output/<id>/audio_upgrade/<hash>/`. It copies metadata, shares only the content-addressed clip cache, and does not update PostgreSQL, restart services, enqueue publishing, upload anything, or overwrite the existing video. `--in-place` explicitly opts into canonical episode metadata. `--height` supports a cheaper preview/test render.

`audio mix` runs the source/repair/asset/mix/master stages against an existing episode. Supply `--episode-dir` for a separate working directory. CLI exit codes: 0 for completed numeric checks, 2 for FAIL, 3 for NEEDS_LISTENING_REVIEW. A video may be rendered for listening review even when publication is blocked. CLI output is not an automatic publish command.

## Story generation and continuity

Existing authored stories are preserved; importing a series bible continues to select only the episode scenes. For a new draft, the optional generator talks only to a locally configured Ollama-compatible endpoint. It will not contact a remote LLM or download a model automatically. Install/run the local model separately if desired. No local LLM runtime is assumed to exist on this machine.

```sh
.venv/bin/python -m studio.cli story generate --brief brief.json --out storage/stories/new-draft.json --outline-only
.venv/bin/python -m studio.cli story generate --brief brief.json --continuity canon.json --out storage/stories/new-draft.json
```

The local model must return three candidate hooks; the engine ranks them and selects one. Defaults: 0–35 s cold open, 35–45 s title/signature, 45–1050 s story, 1050–1155 s climax, 1155–1200 s cliffhanger; mini-hooks at 300/600/900 s. The reference structure is configurable and scales to measured TTS duration. A short source remains short and is reported as such; no content padding, forced playback stretching, or claim of a 20-minute episode.

Generated drafts need author review. The engine catches empty/repetitive prose, obvious greeting hooks, explicit continuity conflicts and duration shortfalls; it cannot prove a script is compelling or infer every semantic contradiction. Store authoritative facts under `continuity` and unresolved questions under `unresolved_questions`. Only approved content commits proposed canon; intentional changes require explicit `continuity_updates`. CLI can also consume a supplied canon JSON file. Facts absent from metadata cannot be reliably checked by simple rules.

## Sound catalog and timing

`config/sound_assets.json` starts empty. The engine does not pretend the requested sound library already exists. Each external asset requires path, SHA-256, source, license and attribution when required. Unknown licenses, missing attribution, tampered source/cache files and decode errors are refused. The whitelist supports original/public-domain, CC0, CC-BY-4.0 and CC-BY-SA-4.0; operators must confirm their rights and satisfy any downstream attribution/share-alike obligations.

Missing assets can use deterministic original procedural waveforms, with source and CC0 metadata. These are **stylized approximations**, not authentic Vietnamese field recordings, human screams, dogs or spoken whispers. No synthetic whisper texture invents dialogue. Disable `AUDIO__PROCEDURAL_FALLBACK` to require a supplied licensed library. Attribution strings are retained in `mix.json` for publishing metadata.

`scene_sound_analysis.json` is a pre-TTS text analysis. `sound_cues.json` is generated only after actual narration timing exists. Cues include scene/cue/asset IDs, trigger, relative position, start/duration, gain, fades, priority and emotional intensity. Keyword rules and a 0–5 tension curve map locations/revelations to ambience, restrained SFX, music and transitions. The thirteenth bell is not rendered as an ordinary audible bell when the text says it cannot be heard/recorded. Authored `sound_cues` can override automatic suggestions with validated timing.

Silence cues default to background-only. They do not erase narration or excuse a missing voice chunk. The analyzer separately subtracts measured paragraph/chunk pauses and explicitly authored all-stem silence. Scene changes use short fades; speech dynamics and intentional quiet moments retain priority.

## Repair and mix policy

Clean sources pass through without default EQ, compression, de-essing or denoising. The analyzer reports sample/true peaks, full-scale flat tops, impulses, known splice discontinuities, low-level stationary-noise/hum/rumble candidates, possible saturation, silence, channel balance/phase, missing chunks, overlap/truncation, actual A/V start/duration differences and mastering format.

An isolated high-confidence sample impulse may be interpolated; known splice boundaries may receive short fades; measured DC may be subtracted. Optional light noise/EQ/compression/de-ess processing requires both a matching measurement and configuration. Native clipped audio is not repaired by normalization. Severe/uncertain source defects are retained and routed to regeneration/listening. Repairs write separate files, preserve before/after metrics, stop when measured quality worsens and never exceed two automatic attempts. This is a conservative repair system, not a general source-restoration guarantee.

The mix exports narration, ambience, music, SFX and audio-signature stems. Internal summation/premaster is float32; stems/master are PCM 24-bit. Narration remains centered. Music/ambience duck 8–14 dB according to speech activity and tension with separate attack/release; SFX also duck and are checked for masking. A uniform premaster gain leaves headroom rather than using heavy limiting to hide an overloaded mix. The renderer consumes the mastered audio once and never adds BGM again.

## QC and listening workflow

Each stage exports:

- `audio_qc_report.json` and `.md`
- `audio_metrics.json`
- `flagged_segments.json`
- bounded `review_clips/*.wav` around critical/uncertain events

Statuses are PASS, WARNING, FAIL and NEEDS_LISTENING_REVIEW. Every flag includes type, interval, severity, confidence, measurements, proposed action and repair status. Numeric checks cannot prove absence of metallic voice texture, all codec artifacts, bad acting or wrong words. New real TTS defaults to a listening-review gate; test tones are explicitly identified as synthetic tests.

Before publication, source, narration, premaster, master and actual encoded reports are mandatory. The gate validates the final file hash and every dependency hash. Replacing a file, stem, script, transcript, cue plan or upstream report invalidates stale evidence. Human review cannot override critical FAIL. A listening decision is bound to report and input SHA-256; editing audio invalidates acceptance.

After actually listening to the relevant clips/file:

```sh
.venv/bin/python -m studio.cli audio review /absolute/path/to/source/audio_qc_report.json \
  --decision accepted --reviewer YOUR_NAME --note 'What was heard and why the uncertain detections are acceptable'
```

Use `--decision rejected` for unresolved pronunciation/rè; regenerate the specific voice segment and rebuild, then listen again. Never manufacture listening approval from an ASR score. The optional earlier ASR audit scripts can help locate candidates, but are not calibrated evidence of pronunciation correctness and cannot silently clear this gate. Re-run final QC/retry the blocked QC job after a valid review. Human publish approval is still separate.

## n8n and debugging

Existing authenticated stage enqueue endpoint remains `/api/episodes/{id}/stages/{stage}`. The new inactive importable `WF-08_audio_production_qc.json` starts at `tts_qc` for an already-generated episode and uses the same body `{episode_id, run, chat_id}`. `AUTO_CHAIN=true` executes subsequent stages through the existing worker. Reusing episode/run returns the same job; no workflow executes shell. No workflow is imported or activated automatically.

Read `/api/episodes/{id}/audio/qc` with `X-API-Key`. The authorized `/api/episodes/{id}/audio/review` endpoint accepts user ID, stage, decision and note and only writes a file-bound listening review. It never publishes. Jobs with `ListeningReviewRequired` become `needs_listening_review`; critical failures retain diagnostic artifacts. Upload checks fresh QC even if a publish record was approved earlier.

If QC fails: inspect the Markdown report and review clips; distinguish intentional sound cues from source defects; fix source/gain/timing rather than lowering detection thresholds blindly. Remaster/rerender when AAC peak exceeds the target. Check `source_chunks.json` for raw model output defects. Preserve raw and repaired candidates for comparison. Do not delete or rewrite accepted evidence to force a PASS.

## Cost and limitations

DSP, procedural assets, cached offline TTS and automated tests cost **0 VND in API credits**. Electricity, disk space, hardware, optional subscription quotas and hosting are separate. The existing monthly budget defaults to 500,000 VND; unknown/paid providers remain blocked without explicit approval and configured pricing. No paid generation is used by this engine.

Local LLM drafting requires a separately available local model; model quality and generation time are machine/model dependent. The shipped procedural sound library is a fallback, not a professionally recorded sound pack. DSP noise/distortion candidates are heuristic and calibrated with synthetic tests; real voices may need human interpretation. Correct Vietnamese pronunciation and professional emotional acting still require listening and sometimes a different source model or regenerated takes. The system is designed to report that uncertainty and block publishing, not certify perfect perceptual quality.

## Asynchronous local story generation and targeted TTS takes

Telegram `/generate_story <ý tưởng>` and authenticated `POST /api/stories/generate` enqueue a bounded `story_generate` job using the configured local model. The API accepts `brief`, optional `continuity`, `chat_id` and `run`. It returns a job ID, so n8n uses the existing `/api/jobs/{id}` polling contract. Generated JSON and Markdown are retained under `storage/stories/generated`, cached by brief/canon/config and imported with rights still pending. No TTS is started and no rights approval is fabricated. Failure to reach a local model is retried at most twice; it never becomes a paid fallback. The generated shape and three distinct hook candidates are validated before import.

A specific generated TTS sentence can be re-taken by explicitly enqueueing the existing `tts` stage with a new `run` and `regenerate_chunks: [1, 2]`. The revision changes only those cache identities and preserves older source files. The chunk numbers must refer to the current `plan_chunks` generation plan; episode-specific older hand-assembled performance plans must be adapted before using this interface. Normal subsequent audio/QC stages run again and listening acceptance is invalidated. Reuse the same run to retry idempotently; use a new run only for a deliberately new take. This is not an unbounded automatic pronunciation retry loop.

The source import allowlist now preserves continuity, explicit continuity updates, unresolved questions, voice cast and authored sound cues. Existing stories remain compatible. Approved canon is checked during script/source validation and serialized with local approval processing. The code does not infer an entire series canon reliably from prose alone.
