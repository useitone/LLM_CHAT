from pathlib import Path

from neurosync_pro.audio.engine import (
    linear_sweep_pcm16_mono,
    sine_pcm16_mono,
    write_wav_pcm16_mono,
)


def test_sine_length() -> None:
    sr = 8000
    pcm = sine_pcm16_mono(440.0, 0.1, sample_rate=sr, volume=0.5)
    assert len(pcm) == int(0.1 * sr) * 2  # 16-bit mono


def test_sweep_and_wav(tmp_path: Path) -> None:
    pcm = linear_sweep_pcm16_mono(200.0, 800.0, 0.05, sample_rate=8000, volume=0.1)
    out = tmp_path / "s.wav"
    write_wav_pcm16_mono(out, pcm, sample_rate=8000)
    assert out.is_file() and out.stat().st_size > 100
