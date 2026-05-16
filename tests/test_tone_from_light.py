from __future__ import annotations

import pytest

from neurosync_pro.light.tone_from_light import rgb_attention_volume_light, rgb_meditation_volume_light


def test_meditation_light_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LIGHT_AUTO_MED_THRESHOLD", "70")
    assert rgb_meditation_volume_light(0.0) == (24, 28, 36)
    assert rgb_meditation_volume_light(1.0) == (100, 150, 255)


def test_meditation_light_threshold_edge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LIGHT_AUTO_MED_THRESHOLD", "70")
    assert rgb_meditation_volume_light(0.69) == (24, 28, 36)
    assert rgb_meditation_volume_light(0.70) == (100, 150, 255)


def test_attention_light_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LIGHT_AUTO_ATT_THRESHOLD", "70")
    lo = rgb_attention_volume_light(0.0)
    hi = rgb_attention_volume_light(1.0)
    assert lo == (24, 28, 36)
    assert hi == (255, 255, 200)


def test_attention_light_threshold_edge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LIGHT_AUTO_ATT_THRESHOLD", "70")
    assert rgb_attention_volume_light(0.69) == (24, 28, 36)
    assert rgb_attention_volume_light(0.70) == (255, 255, 200)


def test_parse_rgb_csv_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    from neurosync_pro.light.tone_from_light import parse_rgb_csv_env

    monkeypatch.delenv("NSP_LIGHT_VOL_LIGHT_MED_ABOVE_RGB", raising=False)
    assert parse_rgb_csv_env("NSP_LIGHT_VOL_LIGHT_MED_ABOVE_RGB", (1, 2, 3)) == (1, 2, 3)


def test_parse_rgb_csv_env_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    from neurosync_pro.light.tone_from_light import parse_rgb_csv_env

    monkeypatch.setenv("NSP_LIGHT_VOL_LIGHT_MED_ABOVE_RGB", "10, 20 , 300")
    assert parse_rgb_csv_env("NSP_LIGHT_VOL_LIGHT_MED_ABOVE_RGB", (0, 0, 0)) == (10, 20, 255)


def test_meditation_light_custom_colors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LIGHT_AUTO_MED_THRESHOLD", "50")
    monkeypatch.setenv("NSP_LIGHT_VOL_LIGHT_MED_ABOVE_RGB", "1,2,3")
    monkeypatch.setenv("NSP_LIGHT_VOL_LIGHT_MED_BELOW_RGB", "4,5,6")
    assert rgb_meditation_volume_light(0.4) == (4, 5, 6)
    assert rgb_meditation_volume_light(0.6) == (1, 2, 3)
