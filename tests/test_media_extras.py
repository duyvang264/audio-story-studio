"""Burned subtitles, Piper TTS and procedural BGM."""
import wave
from pathlib import Path

import pytest

from studio import media, subtitles
from studio.bgm import generate_dark_ambient
from studio.config import PROJECT_ROOT
from studio.tts.base import SAMPLE_RATE, get_tts_provider

pytestmark = pytest.mark.media
PIPER_MODEL = PROJECT_ROOT / "storage/models/piper/vi_VN-vais1000-medium.onnx"


def test_draw_cue_vietnamese_has_ink_in_band():
    img = subtitles.draw_cue("Đêm ấy, gió từ Vực Tro thổi về\nmang theo tiếng chuông", 640, 360)
    assert img.size == (640, subtitles.band_height(360))
    assert img.getbbox() is not None  # something was drawn


def test_build_overlay_covers_full_duration(tmp_path):
    cues = [(0.5, 2.0, "Một"), (2.0, 3.5, "Hai"), (4.0, 5.0, "Ba")]
    lst, y = subtitles.build_overlay(cues, 320, 180, 6.0, tmp_path)
    durations = [float(l.split()[1]) for l in lst.read_text().splitlines() if l.startswith("duration")]
    assert abs(sum(durations) - 6.0) < 1e-6
    assert y == 180 - subtitles.band_height(180)


@pytest.mark.skipif(not PIPER_MODEL.exists(), reason="Piper Vietnamese model not downloaded")
def test_piper_vietnamese_output_format(tmp_path):
    tts = get_tts_provider("piper", str(PIPER_MODEL), 100)
    out = tmp_path / "a.wav"
    tts.synthesize("Xin chào, đây là giọng đọc tiếng Việt.", out)
    with wave.open(str(out)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, SAMPLE_RATE)
        assert w.getnframes() > SAMPLE_RATE  # > 1 s of speech


def test_generate_bgm(tmp_path):
    out = generate_dark_ambient(tmp_path / "bgm.m4a", seconds=12)
    assert abs(media.duration(out) - 12) < 0.2
