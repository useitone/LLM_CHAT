"""Basic unit tests for the realtime stream generator (no audio device)."""

from __future__ import annotations

import numpy as np

from neurosync_pro.audio.stream import StreamConfig, ToneSweepStream


def test_tone_sweep_callback_shapes() -> None:
    s = ToneSweepStream(StreamConfig(sample_rate=48000, channels=1))
    # Directly call callback with a dummy buffer (without opening PortAudio).
    out = np.zeros((256, 1), dtype=np.float32)
    s.play_tone(440.0)
    s._callback(out, 256, None, None)  # type: ignore[arg-type]
    assert out.shape == (256, 1)
    assert float(np.max(np.abs(out))) > 0.0


def test_sweep_runs_and_outputs_nonzero() -> None:
    s = ToneSweepStream(StreamConfig(sample_rate=48000, channels=1))
    out = np.zeros((512, 1), dtype=np.float32)
    s.play_sweep(f0_hz=200, f1_hz=400, duration_s=2.0, log=False, loop=False)
    s._callback(out, 512, None, None)  # type: ignore[arg-type]
    assert float(np.max(np.abs(out))) > 0.0

