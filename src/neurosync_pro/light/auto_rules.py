"""Порядковые правила AUTO для света (LedMatrix §4, упрощённая схема).

Файл JSON: объект ``{"idle": [r,g,b], "rules": [ ... ]}`` или только массив ``rules``.
Каждое правило: ``metric`` (``meditation`` | ``attention``), ``op`` (``>=`` | ``>``),
``value`` (0…100), ``rgb`` — первое совпадение по порядку выигрывает; иначе ``idle``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AutoLightRule:
    metric: str  # meditation | attention
    op: str  # >= | >
    value: float
    rgb: tuple[int, int, int]


def _clamp_rgb(t: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(x))) for x in t)  # type: ignore[misc]


def _parse_rule(obj: Any) -> AutoLightRule | None:
    if not isinstance(obj, dict):
        return None
    m = str(obj.get("metric") or "").strip().lower()
    if m not in ("meditation", "attention"):
        return None
    op = str(obj.get("op") or ">=").strip()
    if op not in (">=", ">"):
        op = ">="
    try:
        val = float(obj.get("value", 0))
    except (TypeError, ValueError):
        return None
    rgb_raw = obj.get("rgb")
    if not isinstance(rgb_raw, (list, tuple)) or len(rgb_raw) != 3:
        return None
    try:
        r, g, b = int(rgb_raw[0]), int(rgb_raw[1]), int(rgb_raw[2])
    except (TypeError, ValueError):
        return None
    return AutoLightRule(metric=m, op=op, value=val, rgb=_clamp_rgb((r, g, b)))


def parse_auto_rules_document(raw: Any) -> tuple[list[AutoLightRule], tuple[int, int, int]]:
    """Parse JSON root (dict with rules+idle, or list of rules)."""

    idle: tuple[int, int, int] = (24, 28, 36)
    rules_src: Any
    if isinstance(raw, dict):
        if "idle" in raw:
            ir = raw.get("idle")
            if isinstance(ir, (list, tuple)) and len(ir) == 3:
                try:
                    idle = _clamp_rgb((int(ir[0]), int(ir[1]), int(ir[2])))
                except (TypeError, ValueError):
                    pass
        rules_src = raw.get("rules", raw.get("auto_rules", []))
    else:
        rules_src = raw
    out: list[AutoLightRule] = []
    if isinstance(rules_src, list):
        for item in rules_src:
            r = _parse_rule(item)
            if r is not None:
                out.append(r)
    return out, idle


def load_auto_rules_from_path(path: str) -> tuple[list[AutoLightRule], tuple[int, int, int]] | None:
    p = (path or "").strip()
    if not p:
        return None
    try:
        text = Path(p).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return None
    rules, idle = parse_auto_rules_document(doc)
    return (rules, idle) if rules else None


def rgb_from_auto_rules(
    attention: float,
    meditation: float,
    rules: list[AutoLightRule],
    idle_rgb: tuple[int, int, int],
) -> tuple[int, int, int]:
    att = float(attention)
    med = float(meditation)
    for rule in rules:
        v = med if rule.metric == "meditation" else att
        ok = v >= rule.value if rule.op == ">=" else v > rule.value
        if ok:
            return rule.rgb
    return idle_rgb


_rules_file_cache: tuple[str, float, tuple[list[AutoLightRule], tuple[int, int, int]] | None] | None = None


def get_auto_rules_from_env() -> tuple[list[AutoLightRule], tuple[int, int, int]] | None:
    """Кэш по mtime файла ``NSP_LIGHT_AUTO_RULES_PATH``."""

    global _rules_file_cache
    path = (os.environ.get("NSP_LIGHT_AUTO_RULES_PATH") or "").strip()
    if not path:
        _rules_file_cache = None
        return None
    try:
        st = os.stat(path)
    except OSError:
        _rules_file_cache = None
        return None
    if _rules_file_cache and _rules_file_cache[0] == path and _rules_file_cache[1] == st.st_mtime:
        return _rules_file_cache[2]
    loaded = load_auto_rules_from_path(path)
    _rules_file_cache = (path, float(st.st_mtime), loaded)
    return loaded
