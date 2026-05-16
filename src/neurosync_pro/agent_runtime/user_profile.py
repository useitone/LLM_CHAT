"""Persist lightweight UI preferences (JSON)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

PROFILE_VERSION = 1


def default_ui_profile_path() -> Path:
    """Default: ~/.neurosync_pro/ui_profile.json"""
    return Path.home() / ".neurosync_pro" / "ui_profile.json"


def resolved_profile_path() -> Path:
    raw = (os.environ.get("NSP_PROFILE_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return default_ui_profile_path()


def default_ui_profile_dict() -> dict[str, Any]:
    return {
        "version": PROFILE_VERSION,
        "ollama_base_url": "",
        "ollama_model": "",
        "chat_free_form": True,
        "chat_auto_apply": False,
        # When False (default), PoC applies JSON immediately — like early «stage 1» UX.
        "chat_agent_runtime_policy": False,
        # «Свободный полёт»: живой диалог + опциональный JSON программатора из ответа.
        "chat_freeflight": False,
        # PoC meditation: iPIXEL / light bridge UI (see meditation_poc).
        "light_ui": {},
    }


def load_ui_profile(path: Path | None = None) -> dict[str, Any]:
    p = path if path is not None else resolved_profile_path()
    base = default_ui_profile_dict()
    if not p.is_file():
        return dict(base)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(base)
    if not isinstance(raw, dict):
        return dict(base)
    out = dict(base)
    for k in base:
        if k in raw:
            out[k] = raw[k]
    try:
        out["version"] = int(raw.get("version", PROFILE_VERSION))
    except (TypeError, ValueError):
        out["version"] = PROFILE_VERSION
    return out


def save_ui_profile(data: dict[str, Any], path: Path | None = None) -> None:
    p = path if path is not None else resolved_profile_path()
    payload = dict(default_ui_profile_dict())
    payload.update(data)
    payload["version"] = PROFILE_VERSION
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
