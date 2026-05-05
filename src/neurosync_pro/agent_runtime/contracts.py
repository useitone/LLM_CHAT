from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .spec_validate import validate_prog_spec, validate_timeline_body

ALLOWED_ACTIONS = {"set_spec", "set_timeline", "hold", "stop"}


_ASSISTANT_REPLY_MAX_LEN = 4000


@dataclass(frozen=True)
class Decision:
    action: str
    spec: str | None
    confidence: float
    reason_code: str
    source: str
    timeline: str | None = None
    assistant_reply: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "action": self.action,
            "spec": self.spec,
            "confidence": self.confidence,
            "reason_code": self.reason_code,
            "source": self.source,
        }
        if self.timeline is not None:
            out["timeline"] = self.timeline
        if self.assistant_reply:
            out["assistant_reply"] = self.assistant_reply
        return out


def safe_hold(reason_code: str, *, source: str) -> Decision:
    return Decision(
        action="hold",
        spec=None,
        confidence=0.0,
        reason_code=reason_code,
        source=source,
        timeline=None,
        assistant_reply=None,
    )


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _coerce_timeline_field(raw: Any) -> str | None:
    """
    Models often emit timeline as JSON array of lines; we accept string or list/tuple of strings.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        s = raw.strip()
        return s if s else None
    if isinstance(raw, (list, tuple)):
        lines: list[str] = []
        for item in raw:
            if isinstance(item, str):
                line = item.strip()
                if line:
                    lines.append(line)
        return "\n".join(lines) if lines else None
    return None


def validate_and_normalize(raw_text: str, *, source: str) -> Decision:
    """Parse provider response and normalize into strict Decision contract."""
    try:
        obj = json.loads(raw_text)
    except json.JSONDecodeError:
        return safe_hold("invalid_json", source=source)

    if not isinstance(obj, dict):
        return safe_hold("invalid_type", source=source)

    action = str(obj.get("action") or "hold").strip().lower()
    if action not in ALLOWED_ACTIONS:
        return safe_hold("invalid_action", source=source)

    spec: str | None = None
    timeline: str | None = None
    if action == "set_spec":
        raw_spec = obj.get("spec")
        if not isinstance(raw_spec, str) or not raw_spec.strip():
            return safe_hold("missing_spec", source=source)
        spec = raw_spec.strip()
        ok_sp, err_sp = validate_prog_spec(spec)
        if not ok_sp:
            return safe_hold(f"invalid_spec:{err_sp}", source=source)
    elif action == "set_timeline":
        raw_tl = obj.get("timeline")
        timeline = _coerce_timeline_field(raw_tl)
        if not timeline:
            return safe_hold("missing_timeline", source=source)
        ok_tl, err_tl = validate_timeline_body(timeline)
        if not ok_tl:
            return safe_hold(f"invalid_timeline:{err_tl}", source=source)

    confidence = max(0.0, min(1.0, _as_float(obj.get("confidence"), 0.0)))
    reason_code = str(obj.get("reason_code") or "model_decision").strip() or "model_decision"
    assistant_reply: str | None = None
    for key in ("assistant_reply", "reply", "say"):
        raw_ar = obj.get(key)
        if isinstance(raw_ar, str):
            s = raw_ar.strip()
            if s:
                assistant_reply = s[:_ASSISTANT_REPLY_MAX_LEN]
            break
    return Decision(
        action=action,
        spec=spec,
        confidence=confidence,
        reason_code=reason_code,
        source=source,
        timeline=timeline,
        assistant_reply=assistant_reply,
    )
