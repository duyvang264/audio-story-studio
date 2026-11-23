"""TTS providers. All produce mono 16-bit PCM WAV at SAMPLE_RATE so chunks concatenate exactly."""
from __future__ import annotations

import math
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

SAMPLE_RATE = 24000


class TTSProvider:
    name = "base"
    model_version = "0"
    is_paid = False

    def __init__(self, voice: str, rate: int):
        self.voice = voice
        self.rate = rate

    def synthesize(self, text: str, out_path: Path) -> None:
        raise NotImplementedError

    def cache_identity(self) -> str:
        return f"{self.name}|{self.model_version}|{self.voice}|{self.rate}"

    def synthesize_many(self, items: list[tuple[str, Path]]) -> None:
        for text, path in items:
            self.synthesize(text, path)


class MacSayProvider(TTSProvider):
    """Local macOS `say` with the Vietnamese 'Linh' voice. Free, offline, adequate for MVP."""
    name = "macos_say"
    model_version = "macos-say-1"

    def __init__(self, voice: str, rate: int):
        super().__init__(voice, rate)
        if shutil.which("say") is None:
            raise RuntimeError("`say` not available (macOS only)")

    def synthesize(self, text: str, out_path: Path) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as f:
            f.write(text)
            txt = f.name
        tmp = out_path.with_suffix(".tmp.wav")
        try:
            subprocess.run(["say", "-v", self.voice, "-r", str(self.rate), "-f", txt, "-o", str(tmp),
                            "--file-format=WAVE", f"--data-format=LEI16@{SAMPLE_RATE}"],
                           check=True, capture_output=True, timeout=300)
            tmp.replace(out_path)
        finally:
            Path(txt).unlink(missing_ok=True)
            tmp.unlink(missing_ok=True)


class ToneProvider(TTSProvider):
    """Deterministic synthetic 'speech' (tone bursts) for fast tests; duration ~ text length."""
    name = "tone"
    model_version = "tone-1"

    def synthesize(self, text: str, out_path: Path) -> None:
        seconds = max(0.4, len(text) / 15.0)
        n = int(seconds * SAMPLE_RATE)
        with wave.open(str(out_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            frames = bytearray()
            for i in range(n):
                env = 0.5 + 0.5 * math.sin(2 * math.pi * 3 * i / SAMPLE_RATE)
                frames += struct.pack("<h", int(6000 * env * math.sin(2 * math.pi * 220 * i / SAMPLE_RATE)))
            w.writeframes(bytes(frames))


class PiperProvider(TTSProvider):
    """Local neural TTS (Piper, ONNX) with the vi_VN-vais1000 voice. Free, offline.

    `voice` is the path to the .onnx model; `rate` is a speed percentage (100 = model default).
    """
    name = "piper"

    def __init__(self, voice: str, rate: int):
        super().__init__(voice, rate)
        try:
            from piper import PiperVoice, SynthesisConfig
        except ImportError as e:
            raise RuntimeError("piper-tts not installed (pip install piper-tts)") from e
        model = Path(voice)
        if not model.exists():
            raise RuntimeError(f"Piper model not found: {model} (python -m piper.download_voices "
                               "vi_VN-vais1000-medium)")
        self._voice = PiperVoice.load(str(model))
        self._syn = SynthesisConfig(length_scale=100 / max(rate, 50))
        self.model_version = f"piper-{model.stem}"

    def cache_identity(self) -> str:
        return f"{self.name}|{self.model_version}|{self.rate}"

    def synthesize(self, text: str, out_path: Path) -> None:
        raw = out_path.with_suffix(".raw.wav")
        try:
            with wave.open(str(raw), "wb") as w:
                self._voice.synthesize_wav(text, w, syn_config=self._syn)
            # Resample to the pipeline's common format so chunks concatenate sample-exactly.
            subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-i", str(raw),
                            "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(out_path)],
                           check=True, capture_output=True, timeout=120)
        finally:
            raw.unlink(missing_ok=True)


class VieNeuProvider(TTSProvider):
    name = "vieneu"
    model_version = "vieneu-3.8.5-offline-fp32-v1"

    def __init__(self, voice, rate, settings=None):
        super().__init__(voice, rate)
        from ..config import get_settings
        self.settings = settings or get_settings()
        self.python = self.settings.project_path(self.settings.vieneu_python)
        if not self.python or not self.python.is_file():
            raise RuntimeError("VieNeu runtime missing; install the optional local runtime, not a paid fallback")

    def synthesize(self, text: str, out_path: Path) -> None:
        self.synthesize_many([(text, out_path)])

    def synthesize_many(self, items: list[tuple[str, Path]]) -> None:
        import json
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as f:
            json.dump({"voice": self.voice, "items": [{"text": t, "path": str(p.resolve())} for t, p in items]}, f, ensure_ascii=False)
            request = Path(f.name)
        try:
            subprocess.run([str(self.python), str(Path(__file__).with_name("vieneu_worker.py")), str(request)],
                           check=True, capture_output=True, timeout=3600)
        finally:
            request.unlink(missing_ok=True)


def get_tts_provider(name: str, voice: str, rate: int, *, settings=None) -> TTSProvider:
    if name == "vieneu":
        return VieNeuProvider(voice, rate, settings)
    providers = {"macos_say": MacSayProvider, "piper": PiperProvider, "tone": ToneProvider}
    if name not in providers:
        raise ValueError(f"unknown TTS provider {name!r}")
    return providers[name](voice, rate)
