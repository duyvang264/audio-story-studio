# Audio upgrade audit and implementation plan

The existing app is a native Python/FastAPI/PostgreSQL worker with a Docker n8n orchestrator, chunk-cached macOS/Piper/tone TTS, frame-based visual planning, FFmpeg renders and a human publishing approval. Existing API paths and stage names remain supported.

Audit findings: only sentence WAV format and minimum duration were checked; one-pass normalization happened before render, and music was remixed differently in long/short renderers. There were no source/master/encoded audio gates, measured true peaks, click/noise candidates, immutable repair evidence, sound license catalog or speech ducking. Publishing trusted an unbound `passed` JSON boolean. Story building imported authored prose rather than generating/validating horror structure. Local VieNeu existed only in episode-specific scripts. PostgreSQL retries/heartbeats and budget safeguards already exist and should be reused. New processing stays on macOS; n8n continues to enqueue asynchronous jobs over the same API.

Implementation order:

1. Add NumPy/FFmpeg analyzer, explicit uncertainty, source-bound reports/reviews and bounded non-destructive repair.
2. Add deterministic original sound fallbacks, checksum/license catalog, measured cue/tension planning, five stems and speech-driven ducking.
3. Centralize two-pass mastering and use its result in both renderers; analyze actual AAC output and recheck provenance immediately before publish approval/upload.
4. Add local-only story generation/continuity validation, offline batch VieNeu adapter and pronunciation substitutions; retain authored stories and existing timing contracts.
5. Extend worker stages, CLI/API/n8n entry points, add synthetic damage/false-positive tests, and run the existing village episode into isolated artifacts. Do not restart services, deploy or publish.

The 20-minute structure is configurable editorial guidance; duration is measured from TTS and short source stories are reported, never padded or time-stretched to pretend they meet the target. Perceptual distortion, pronunciation and acting remain listening-review concerns.
