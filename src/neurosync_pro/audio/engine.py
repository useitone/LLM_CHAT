"""Simple tone / linear sweep synthesis and WAV export (stdlib only)."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path


def sine_pcm16_mono(
    freq_hz: float,
    duration_s: float,
    sample_rate: int = 44100,
    volume: float = 0.25,
) -> bytes:
    """16-bit little-endian mono PCM samples."""
    n = max(1, int(duration_s * sample_rate))
    amp = max(0.0, min(1.0, volume)) * 32767.0
    chunks: list[bytes] = []
    for i in range(n):
        t = i / sample_rate
        v = int(amp * math.sin(2.0 * math.pi * freq_hz * t))
        v = max(-32768, min(32767, v))
        chunks.append(struct.pack("<h", v))
    return b"".join(chunks)


def linear_sweep_pcm16_mono(
    f0_hz: float,
    f1_hz: float,
    duration_s: float,
    sample_rate: int = 44100,
    volume: float = 0.25,
) -> bytes:
    """Linear frequency sweep (instantaneous f ramps f0→f1)."""
    n = max(2, int(duration_s * sample_rate))
    amp = max(0.0, min(1.0, volume)) * 32767.0
    phase = 0.0
    chunks: list[bytes] = []
    for i in range(n):
        f = f0_hz + (f1_hz - f0_hz) * (i / (n - 1))
        phase += 2.0 * math.pi * f / sample_rate
        v = int(amp * math.sin(phase))
        v = max(-32768, min(32767, v))
        chunks.append(struct.pack("<h", v))
    return b"".join(chunks)


def write_wav_pcm16_mono(path: Path, pcm: bytes, sample_rate: int = 44100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
