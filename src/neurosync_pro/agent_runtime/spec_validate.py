"""Strict validation for Programmer spec / timeline (matches PoC parser semantics)."""

from __future__ import annotations

_OFF_TOKENS = frozenset({"-", "off", "idle"})
_NOISE_COLORS = frozenset({"white", "pink", "brown"})


def parse_mmss(ts: str) -> float | None:
    """Parse m:ss, mm:ss, or hh:mm:ss into seconds from zero."""
    try:
        bits = [int(b) for b in ts.strip().split(":")]
    except ValueError:
        return None
    if len(bits) == 2:
        m, s = bits
        return float(m * 60 + s)
    if len(bits) == 3:
        h, m, s = bits
        return float(h * 3600 + m * 60 + s)
    return None


def _parse_sweep_token(p: str) -> bool:
    if not p.startswith("sweep:"):
        return False
    try:
        rhs = p[len("sweep:") :]
        arrow = rhs.split("->", 1)
        if len(arrow) != 2:
            return False
        f0 = float(arrow[0])
        tail = arrow[1]
        bits = tail.split("/")
        f1 = float(bits[0])
        dur = float(bits[1]) if len(bits) > 1 else 10.0
        amp = float(bits[2]) if len(bits) > 2 else 0.6
        if dur <= 0 or f0 <= 0 or f1 <= 0:
            return False
        if amp > 1.0:
            amp = amp / 100.0
        if amp < 0.0 or amp > 1.0:
            return False
    except (ValueError, IndexError):
        return False
    return True


def _parse_binaural_token(p: str) -> bool:
    if "+" not in p or "/" not in p:
        return False
    try:
        left, amp_raw = p.split("/", 1)
        carrier_raw, beat_raw = left.split("+", 1)
        carrier = float(carrier_raw)
        beat = float(beat_raw)
        amp = float(amp_raw)
        if carrier <= 0 or beat <= 0:
            return False
        if amp > 1.0:
            amp = amp / 100.0
        if amp < 0.0 or amp > 1.0:
            return False
    except (ValueError, IndexError):
        return False
    return True


def _parse_noise_token(p: str) -> bool:
    if "+" in p:
        return False
    if "/" not in p:
        return False
    try:
        c_raw, a_raw = p.split("/", 1)
        color = str(c_raw).lower().strip()
        if color not in _NOISE_COLORS:
            return False
        vol = float(a_raw)
        if vol > 1.0:
            vol = vol / 100.0
        if vol < 0.0 or vol > 1.0:
            return False
    except (ValueError, IndexError):
        return False
    return True


def validate_prog_spec(spec: str) -> tuple[bool, str]:
    """
    Every whitespace-separated token must be recognized.
    Returns (True, "") or (False, reason_code).
    """
    s = (spec or "").strip()
    if not s:
        return False, "empty_spec"
    saw_effect = False
    for p in s.split():
        p = p.strip()
        if not p:
            continue
        if p in _OFF_TOKENS:
            saw_effect = True
            continue
        if _parse_sweep_token(p) or _parse_binaural_token(p) or _parse_noise_token(p):
            saw_effect = True
            continue
        return False, f"unknown_token:{p}"
    if not saw_effect:
        return False, "no_recognized_tokens"
    return True, ""


def validate_timeline_body(text: str) -> tuple[bool, str]:
    """Validate timeline lines: '<timestamp> <spec>' per programmer_commands.md."""
    raw_text = (text or "").strip()
    if not raw_text:
        return False, "empty_timeline"
    any_step = False
    for raw in raw_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "#" in line:
            line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            return False, "timeline_missing_spec"
        ts, spec_line = parts[0].strip(), parts[1].strip()
        if parse_mmss(ts) is None:
            return False, f"bad_timestamp:{ts}"
        ok, reason = validate_prog_spec(spec_line)
        if not ok:
            return False, f"bad_spec:{reason}"
        any_step = True
    if not any_step:
        return False, "no_timeline_steps"
    return True, ""
