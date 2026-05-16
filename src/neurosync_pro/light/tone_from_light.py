"""RGB для EEG→Tone «Volume + Light» (Mono): пороги как у авто-света, но только по одной метрике.

Цвета задаются переменными ``NSP_LIGHT_VOL_LIGHT_*_RGB`` (строка ``r,g,b``); см. docs/light-ipixel.md.
"""

from __future__ import annotations

import os


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


def parse_rgb_csv_env(key: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
    """Parse ``r,g,b`` from ``os.environ[key]``; clamp 0…255; invalid → ``default``."""

    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 3:
        return default
    out: list[int] = []
    for p in parts:
        try:
            out.append(max(0, min(255, int(float(p)))))
        except (TypeError, ValueError):
            return default
    return (out[0], out[1], out[2])


def _norm01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else float(x)


def rgb_meditation_volume_light(med_0_1: float) -> tuple[int, int, int]:
    """Свет только по Meditation: при M ≥ ``NSP_LIGHT_AUTO_MED_THRESHOLD`` (0…100) — above, иначе below."""

    thr = _float_env("NSP_LIGHT_AUTO_MED_THRESHOLD", 70.0) / 100.0
    hi = parse_rgb_csv_env("NSP_LIGHT_VOL_LIGHT_MED_ABOVE_RGB", (100, 150, 255))
    lo = parse_rgb_csv_env("NSP_LIGHT_VOL_LIGHT_MED_BELOW_RGB", (24, 28, 36))
    if _norm01(med_0_1) >= thr:
        return hi
    return lo


def rgb_attention_volume_light(att_0_1: float) -> tuple[int, int, int]:
    """Свет только по Attention: при A ≥ ``NSP_LIGHT_AUTO_ATT_THRESHOLD`` — above, иначе below."""

    thr = _float_env("NSP_LIGHT_AUTO_ATT_THRESHOLD", 70.0) / 100.0
    hi = parse_rgb_csv_env("NSP_LIGHT_VOL_LIGHT_ATT_ABOVE_RGB", (255, 255, 200))
    lo = parse_rgb_csv_env("NSP_LIGHT_VOL_LIGHT_ATT_BELOW_RGB", (24, 28, 36))
    if _norm01(att_0_1) >= thr:
        return hi
    return lo
