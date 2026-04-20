"""Audio synthesis (tone, sweep) and WAV export."""

from neurosync_pro.audio.engine import (
    linear_sweep_pcm16_mono,
    sine_pcm16_mono,
    write_wav_pcm16_mono,
)
try:  # optional extras
    from neurosync_pro.audio.stream import StreamConfig, ToneSweepStream
except Exception:  # pragma: no cover
    StreamConfig = None  # type: ignore[misc,assignment]
    ToneSweepStream = None  # type: ignore[misc,assignment]

__all__ = [
    "linear_sweep_pcm16_mono",
    "sine_pcm16_mono",
    "StreamConfig",
    "ToneSweepStream",
    "write_wav_pcm16_mono",
]
