"""TTS Engine: chunked synthesis with content-hash cache, assembly, loudness normalisation,
and transcript timing derived from real chunk durations."""
from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path

from . import budget, media
from .config import Settings, get_settings
from .db import connect
from .tts.base import SAMPLE_RATE, get_tts_provider
from .tts.text import spoken_text

LEAD_IN_SEC = 0.6
PAUSE_SENTENCE = 0.25
PAUSE_PARAGRAPH = 0.6
PAUSE_SECTION = 1.2
TAIL_SEC = 1.0


def chunk_cache_path(storage: Path, identity: str, text: str) -> tuple[str, Path]:
    h = hashlib.sha256(f"{identity}|{text}".encode("utf-8")).hexdigest()
    return h, storage / "audio" / "cache" / h[:2] / f"{h}.wav"


def _wav_frames(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != SAMPLE_RATE:
            raise media.MediaError(f"unexpected wav format in {path}")
        return w.readframes(w.getnframes())


def plan_chunks(script: dict) -> list[dict]:
    chunks = []
    sections = script["sections"]
    for si, sec in enumerate(sections):
        for pi, para in enumerate(sec["paragraphs"]):
            for ti, sent in enumerate(para["sentences"]):
                last_sent = ti == len(para["sentences"]) - 1
                last_para = pi == len(sec["paragraphs"]) - 1
                if not last_sent:
                    pause = PAUSE_SENTENCE
                elif not last_para:
                    pause = PAUSE_PARAGRAPH
                elif si < len(sections) - 1:
                    pause = PAUSE_SECTION
                else:
                    pause = TAIL_SEC
                chunks.append({"section_no": sec["section_no"], "paragraph_no": para["paragraph_no"],
                               "text": sent, "pause_after": pause,
                               "voice": para.get("voice") or script.get("voice_cast", {}).get(para.get("speaker"))})
    for i, ch in enumerate(chunks):
        ch["chunk_no"] = i + 1
    return chunks


def produce_narration(episode_id: int, *, job_id: int | None = None, settings: Settings | None = None,
                      regenerate_chunks: list[int] | None = None, regeneration_key: str | None = None) -> dict:
    s = settings or get_settings()
    ep_dir = s.storage / "episodes" / str(episode_id)
    script = json.loads((ep_dir / "episode_script.json").read_text(encoding="utf-8"))
    provider = get_tts_provider(*s.tts_args(), settings=s)
    identity = provider.cache_identity()
    chunks = plan_chunks(script)
    if not chunks:
        raise ValueError("script has no narration text")
    forced = set(regenerate_chunks or [])
    if forced and (not regeneration_key or not forced <= {ch['chunk_no'] for ch in chunks}):
        raise ValueError('Chunk regeneration requires valid generation-plan chunk numbers and an explicit revision key')

    res = budget.reserve("tts", provider.name, units=len(chunks), job_id=job_id, episode_id=episode_id, settings=s)
    cache_hits = 0
    try:
        pending = {}
        for ch in chunks:
            synthesis, issues = spoken_text(ch["text"], s.tts_pronunciations)
            ch["synthesis_text"], ch["pronunciation_review"] = synthesis, issues
            chosen = ch["voice"] or provider.voice
            ch["voice"] = chosen
            voice_identity = identity if chosen == provider.voice else identity + "|voice=" + chosen
            if ch['chunk_no'] in forced:
                voice_identity += '|regenerate=' + str(regeneration_key)
            h, path = chunk_cache_path(s.storage, voice_identity, synthesis)
            ch["text_hash"], ch["audio_path"] = h, str(path)
            if path.exists() and path.stat().st_size > 1000:
                cache_hits += 1
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                pending.setdefault(chosen, []).append((synthesis, path))
        for voice, items in pending.items():
            adapter = provider if voice == provider.voice else get_tts_provider(s.tts_provider, voice, provider.rate, settings=s)
            adapter.synthesize_many(list(dict.fromkeys(items)))
        for ch in chunks:
            path = Path(ch["audio_path"])
            n = len(_wav_frames(path)) // 2
            if n < SAMPLE_RATE * 0.15:
                path.unlink(missing_ok=True)
                raise media.MediaError(f"chunk {ch['chunk_no']} produced near-empty audio")
            ch["samples"] = n
    except Exception:
        budget.release(res, note="tts failed")
        raise
    budget.commit(res, note=f"{len(chunks)} chunks, {cache_hits} cache hits")

    audio_dir = s.storage / "audio" / "episodes" / str(episode_id)
    audio_dir.mkdir(parents=True, exist_ok=True)
    from .audio_engine.io import digest
    revision = hashlib.sha256(json.dumps([identity, [(ch["text_hash"], digest(ch['audio_path'])) for ch in chunks], "assembly-v2"]).encode()).hexdigest()[:20]
    audio_dir = audio_dir / "sources" / revision
    audio_dir.mkdir(parents=True, exist_ok=True)
    raw = audio_dir / "narration_raw.wav"
    cursor = int(LEAD_IN_SEC * SAMPLE_RATE)
    temporary_raw = raw.with_suffix(".part.wav")
    with wave.open(str(temporary_raw), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(b"\x00\x00" * cursor)
        for ch in chunks:
            out.writeframes(_wav_frames(Path(ch["audio_path"])))
            ch["start"] = round(cursor / SAMPLE_RATE, 3)
            cursor += ch["samples"]
            ch["end"] = round(cursor / SAMPLE_RATE, 3)
            pause = int(ch["pause_after"] * SAMPLE_RATE)
            out.writeframes(b"\x00\x00" * pause)
            cursor += pause
    total = cursor / SAMPLE_RATE
    if raw.exists():
        temporary_raw.unlink()
    else:
        temporary_raw.replace(raw)

    master = audio_dir / "narration.wav"
    if not master.exists():
        media.ffmpeg(["-i", str(raw), "-ar", "48000", "-ac", "1", "-c:a", "pcm_s24le", str(master)])
    master_dur = media.duration(master)
    if abs(master_dur - total) > 0.1:
        raise media.MediaError(f"normalised narration duration drifted: {master_dur} vs {total}")

    paragraphs: dict[int, dict] = {}
    for ch in chunks:
        p = paragraphs.setdefault(ch["paragraph_no"], {"paragraph_no": ch["paragraph_no"],
                                                       "section_no": ch["section_no"], "start": ch["start"]})
        p["end"] = ch["end"]
        p["pause_after"] = ch["pause_after"]
    timeline = {
        "episode_id": episode_id,
        "duration_sec": round(total, 3),
        "lead_in_sec": LEAD_IN_SEC,
        "audio_path": str(master),
        "source_audio_path": str(raw),
        "pronunciation_review_required": any(ch["pronunciation_review"] for ch in chunks),
        "tts": identity,
        "timing_method": "chunk_duration",   # sentence-level; forced alignment not used
        "chunks": [{k: ch[k] for k in ("chunk_no", "section_no", "paragraph_no", "text", "synthesis_text", "voice", "audio_path", "start", "end", "pause_after")}
                   for ch in chunks],
        "paragraphs": list(paragraphs.values()),
    }
    (ep_dir / "transcript_timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")

    with connect() as c:
        c.execute("DELETE FROM narration_chunks WHERE episode_id=%s", (episode_id,))
        with c.cursor() as cur:
            cur.executemany("""
                INSERT INTO narration_chunks (episode_id, chunk_no, section_no, paragraph_no, text, text_hash, voice,
                    model_version, audio_path, duration_sec, start_sec, end_sec, pause_after_sec)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(episode_id, ch["chunk_no"], ch["section_no"], ch["paragraph_no"], ch["text"], ch["text_hash"],
                  ch["voice"], provider.model_version, ch["audio_path"], ch["samples"] / SAMPLE_RATE,
                  ch["start"], ch["end"], ch["pause_after"]) for ch in chunks])
        c.execute("UPDATE episodes SET narration_duration_sec=%s, updated_at=now() WHERE id=%s", (total, episode_id))
    return {"duration_sec": round(total, 3), "chunks": len(chunks), "cache_hits": cache_hits, "audio": str(master)}
