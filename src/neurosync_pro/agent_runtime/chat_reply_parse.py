"""Extract executable program JSON from mixed assistant chat replies (free-form + fenced blocks)."""

from __future__ import annotations

import re
from collections.abc import Callable

from .contracts import Decision, validate_and_normalize


def strip_md_json_fence(raw: str) -> str:
    """If the whole reply is wrapped in ```json ... ```, return inner text; else unchanged."""
    s = (raw or "").strip()
    if not s.startswith("```"):
        return s
    lines = s.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def try_parse_ready_program_decision(
    raw: str,
    *,
    command_ready: Callable[[Decision], bool],
    source: str = "chat_ui",
) -> Decision | None:
    """
    Try whole message, fenced trim, then ```json``` blocks (last block first — command often at end).
    """
    s = (raw or "").strip()
    if not s:
        return None
    candidates: list[str] = [s]
    fenced = strip_md_json_fence(s)
    if fenced != s:
        candidates.append(fenced)
    blocks = list(re.finditer(r"```(?:json)?\s*([\s\S]*?)```", s, re.IGNORECASE))
    for m in reversed(blocks):
        inner = m.group(1).strip()
        if inner:
            candidates.append(inner)
    seen: set[str] = set()
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        d = validate_and_normalize(c, source=source)
        if command_ready(d):
            return d
    return None
