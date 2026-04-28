"""Realtime audio output via sounddevice (PortAudio).

This module is Windows-first and provides minimal primitives for:
- continuous tone
- linear/log sweep

Design: callback-based OutputStream; parameters can be updated from UI/CLI thread.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

import numpy as np

sd = None  # lazy import in start(); keeps package importable without audio extras


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _db_to_amp(db: float) -> float:
    return float(10.0 ** (db / 20.0))


@dataclass
class StreamConfig:
    sample_rate: int = 48000
    channels: int = 1
    blocksize: int = 0  # let PortAudio decide
    dtype: str = "float32"


class ToneSweepStream:
    """One stream that can play tone or sweep (exclusive)."""

    def __init__(self, cfg: StreamConfig | None = None) -> None:
        self.cfg = cfg or StreamConfig()
        self._lock = threading.Lock()

        self._mode: str = "idle"  # idle|tone|sweep|binaural|noise
        self._volume = 0.15
        self._volume_l = 0.15
        self._volume_r = 0.15
        self._phase = 0.0
        self._phase_r = 0.0

        # tone params
        self._tone_hz = 440.0
        self._tone_hz_r = 440.0

        # sweep params
        self._sweep_f0 = 200.0
        self._sweep_f1 = 1000.0
        self._sweep_dur = 10.0
        self._sweep_log = False
        self._sweep_loop = False
        self._sweep_start_t: float | None = None

        # envelope (avoid clicks)
        self._fade_in = 0.02
        self._fade_out = 0.05

        # noise params
        self._noise_start_t: float | None = None
        self._noise_stop_t: float | None = None
        self._noise_rng = np.random.default_rng()
        self._noise_color: str = "white"  # white|pink|brown
        # filter state (per channel) for colored noise
        self._pink_state = None
        self._brown_state = None

        self._stream = None

    def start(self) -> None:
        global sd
        if self._stream is not None:
            return
        if sd is None:
            try:
                import sounddevice as _sd
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "Missing dependency: sounddevice. Install extras: pip install -e \".[audio]\""
                ) from exc
            sd = _sd
        self._stream = sd.OutputStream(
            samplerate=self.cfg.sample_rate,
            channels=self.cfg.channels,
            dtype=self.cfg.dtype,
            blocksize=self.cfg.blocksize,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        st = self._stream
        self._stream = None
        if st is not None:
            st.stop()
            st.close()
        with self._lock:
            self._mode = "idle"
            self._sweep_start_t = None

    def set_volume(self, volume: float) -> None:
        with self._lock:
            v = _clamp(float(volume), 0.0, 1.0)
            self._volume = v
            self._volume_l = v
            self._volume_r = v

    def set_volume_lr(self, left: float, right: float) -> None:
        """Set per-channel volume (0..1). If output is mono, volume is effectively averaged."""
        with self._lock:
            vl = _clamp(float(left), 0.0, 1.0)
            vr = _clamp(float(right), 0.0, 1.0)
            self._volume_l = vl
            self._volume_r = vr
            self._volume = (vl + vr) * 0.5

    def set_fades(self, fade_in_s: float, fade_out_s: float) -> None:
        with self._lock:
            self._fade_in = max(0.0, float(fade_in_s))
            self._fade_out = max(0.0, float(fade_out_s))

    def play_tone(self, freq_hz: float) -> None:
        with self._lock:
            self._tone_hz = max(1.0, float(freq_hz))
            self._mode = "tone"
            self._sweep_start_t = None

    def play_binaural(self, left_hz: float, right_hz: float) -> None:
        """Stereo tone: left and right frequencies may differ (binaural beats with headphones)."""
        with self._lock:
            self._tone_hz = max(1.0, float(left_hz))
            self._tone_hz_r = max(1.0, float(right_hz))
            self._mode = "binaural"
            self._sweep_start_t = None

    def play_sweep(
        self,
        *,
        f0_hz: float,
        f1_hz: float,
        duration_s: float,
        log: bool,
        loop: bool,
    ) -> None:
        with self._lock:
            self._sweep_f0 = max(1.0, float(f0_hz))
            self._sweep_f1 = max(1.0, float(f1_hz))
            self._sweep_dur = max(0.05, float(duration_s))
            self._sweep_log = bool(log)
            self._sweep_loop = bool(loop)
            self._mode = "sweep"
            self._sweep_start_t = time.monotonic()

    def idle(self) -> None:
        with self._lock:
            if self._mode == "noise":
                # Fade out noise without clicks.
                self._noise_stop_t = time.monotonic()
                return
            self._mode = "idle"
            self._sweep_start_t = None

    def play_noise(self, *, seed: int | None = None, color: str = "white") -> None:
        """Play noise continuously (mono or stereo depending on stream channels).

        color: white|pink|brown
        """
        with self._lock:
            if seed is not None:
                self._noise_rng = np.random.default_rng(int(seed))
            c = str(color or "white").lower().strip()
            if c not in ("white", "pink", "brown"):
                c = "white"
            self._noise_color = c
            self._mode = "noise"
            self._sweep_start_t = None
            self._noise_start_t = time.monotonic()
            self._noise_stop_t = None
            # reset filter states on restart
            self._pink_state = None
            self._brown_state = None

    @staticmethod
    def _pink_filter(x: np.ndarray, state: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        # Paul Kellet's refined pink noise filter (approx 1/f).
        # state: shape (7,)
        b0, b1, b2, b3, b4, b5, b6 = [float(v) for v in state.tolist()]
        y = np.empty_like(x, dtype=np.float32)
        for i in range(x.shape[0]):
            w = float(x[i])
            b0 = 0.99886 * b0 + w * 0.0555179
            b1 = 0.99332 * b1 + w * 0.0750759
            b2 = 0.96900 * b2 + w * 0.1538520
            b3 = 0.86650 * b3 + w * 0.3104856
            b4 = 0.55000 * b4 + w * 0.5329522
            b5 = -0.7616 * b5 - w * 0.0168980
            out = b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362
            b6 = w * 0.115926
            y[i] = np.float32(out)
        st = np.array([b0, b1, b2, b3, b4, b5, b6], dtype=np.float32)
        return y, st

    @staticmethod
    def _brown_filter(x: np.ndarray, state: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        # Brown(ian) noise via leaky integrator.
        # state: shape (1,)
        s = float(state[0])
        y = np.empty_like(x, dtype=np.float32)
        leak = 0.999  # prevents drift
        for i in range(x.shape[0]):
            s = leak * s + float(x[i]) * 0.02
            # clamp to avoid runaway
            if s > 1.0:
                s = 1.0
            elif s < -1.0:
                s = -1.0
            y[i] = np.float32(s)
        return y, np.array([s], dtype=np.float32)

    def _callback(self, outdata: np.ndarray, frames: int, _time_info, status) -> None:  # noqa: ANN001
        if status:
            # Dropouts are expected sometimes; keep stream alive.
            pass

        sr = float(self.cfg.sample_rate)
        t = (np.arange(frames, dtype=np.float32) / sr).astype(np.float32)

        with self._lock:
            mode = self._mode
            vol = self._volume
            vol_l = self._volume_l
            vol_r = self._volume_r
            phase0 = self._phase

            tone_hz = self._tone_hz

            f0 = self._sweep_f0
            f1 = self._sweep_f1
            dur = self._sweep_dur
            is_log = self._sweep_log
            loop = self._sweep_loop
            start_t = self._sweep_start_t

            fade_in = self._fade_in
            fade_out = self._fade_out

        if mode == "idle":
            outdata[:] = 0.0
            return

        if mode == "tone":
            # Constant frequency.
            phase = phase0 + (2.0 * math.pi * float(tone_hz)) * t
            y = np.sin(phase, dtype=np.float32)
            # Keep phase continuity.
            phase_end = float(phase[-1] + (2.0 * math.pi * float(tone_hz)) / sr)
            with self._lock:
                self._phase = phase_end % (2.0 * math.pi)
            y *= np.float32(vol)
            outdata[:, 0] = y
            if outdata.shape[1] > 1:
                outdata[:, 1] = y
            return

        if mode == "binaural":
            # Stereo constant frequencies.
            phase_l = phase0 + (2.0 * math.pi * float(tone_hz)) * t
            y_l = np.sin(phase_l, dtype=np.float32)
            phase_end_l = float(phase_l[-1] + (2.0 * math.pi * float(tone_hz)) / sr)

            with self._lock:
                phase0_r = self._phase_r
                tone_hz_r = self._tone_hz_r

            phase_r = phase0_r + (2.0 * math.pi * float(tone_hz_r)) * t
            y_r = np.sin(phase_r, dtype=np.float32)
            phase_end_r = float(phase_r[-1] + (2.0 * math.pi * float(tone_hz_r)) / sr)

            with self._lock:
                self._phase = phase_end_l % (2.0 * math.pi)
                self._phase_r = phase_end_r % (2.0 * math.pi)

            y_l *= np.float32(vol_l)
            y_r *= np.float32(vol_r)
            outdata[:, 0] = y_l
            if outdata.shape[1] > 1:
                outdata[:, 1] = y_r
            else:
                outdata[:, 0] = (y_l + y_r) * np.float32(0.5)
            return

        if mode == "noise":
            # Noise (white/pink/brown). Use fade-in/out envelope to avoid clicks.
            with self._lock:
                rng = self._noise_rng
                ns = self._noise_start_t
                ne = self._noise_stop_t
                color = self._noise_color
            now = time.monotonic()

            env = 1.0
            if fade_in > 0 and ns is not None:
                env *= _clamp((now - ns) / float(fade_in), 0.0, 1.0)
            if ne is not None and fade_out > 0:
                env *= _clamp((float(fade_out) - (now - ne)) / float(fade_out), 0.0, 1.0)
                if env <= 0.0:
                    self.idle()
                    outdata[:] = 0.0
                    return

            ch = int(outdata.shape[1])
            if ch > 1:
                y = rng.standard_normal((frames, ch), dtype=np.float32)
                if color != "white":
                    # per-channel colored filtering
                    if color == "pink":
                        if self._pink_state is None or int(getattr(self._pink_state, "shape", [0])[0]) != (7 * ch):
                            self._pink_state = np.zeros((ch, 7), dtype=np.float32)
                        for c in range(ch):
                            y[:, c], self._pink_state[c] = self._pink_filter(y[:, c], self._pink_state[c])
                    elif color == "brown":
                        if self._brown_state is None or int(getattr(self._brown_state, "shape", [0])[0]) != ch:
                            self._brown_state = np.zeros((ch, 1), dtype=np.float32)
                        for c in range(ch):
                            y[:, c], self._brown_state[c] = self._brown_filter(y[:, c], self._brown_state[c])
                y[:, 0] *= np.float32(vol_l)
                y[:, 1] *= np.float32(vol_r)
                if ch > 2:
                    y[:, 2:] *= np.float32(vol)
                outdata[:] = y * np.float32(env)
            else:
                y = rng.standard_normal(frames, dtype=np.float32)
                if color == "pink":
                    if self._pink_state is None:
                        self._pink_state = np.zeros((1, 7), dtype=np.float32)
                    y, self._pink_state[0] = self._pink_filter(y, self._pink_state[0])
                elif color == "brown":
                    if self._brown_state is None:
                        self._brown_state = np.zeros((1, 1), dtype=np.float32)
                    y, self._brown_state[0] = self._brown_filter(y, self._brown_state[0])
                outdata[:, 0] = (y * np.float32(vol)) * np.float32(env)
            return

        # Sweep mode.
        now = time.monotonic()
        if start_t is None:
            start_t = now
            with self._lock:
                self._sweep_start_t = start_t

        prog0 = (now - start_t)
        # Per-sample absolute time (seconds since sweep start).
        prog = prog0 + t

        done_mask = prog >= dur
        if done_mask.all():
            if loop:
                with self._lock:
                    self._sweep_start_t = now
                outdata[:] = 0.0
            else:
                self.idle()
                outdata[:] = 0.0
            return

        # Compute instantaneous frequency.
        x = np.clip(prog / dur, 0.0, 1.0).astype(np.float32)
        if is_log:
            # log sweep: f = f0 * (f1/f0)^x
            ratio = np.float32(f1 / f0)
            freq = np.float32(f0) * np.power(ratio, x, dtype=np.float32)
        else:
            freq = np.float32(f0) + (np.float32(f1 - f0) * x)

        # Phase accumulator: integrate frequency.
        # Approx: phase[n] = phase0 + 2π * cumsum(freq)/sr
        inc = (2.0 * math.pi) * (freq / np.float32(sr))
        phase = phase0 + np.cumsum(inc, dtype=np.float32)
        y = np.sin(phase, dtype=np.float32)

        # Envelope: fade in at sweep start, fade out near end.
        env = np.ones(frames, dtype=np.float32)
        if fade_in > 0:
            env *= np.clip(prog / np.float32(fade_in), 0.0, 1.0)
        if fade_out > 0:
            env *= np.clip((np.float32(dur) - prog) / np.float32(fade_out), 0.0, 1.0)
        y *= env

        # Zero out samples beyond duration in this block.
        if done_mask.any():
            y = y.copy()
            y[done_mask] = 0.0

        phase_end = float(phase[-1] + float(inc[-1]))
        with self._lock:
            self._phase = phase_end % (2.0 * math.pi)

        y *= np.float32(vol)
        outdata[:, 0] = y

