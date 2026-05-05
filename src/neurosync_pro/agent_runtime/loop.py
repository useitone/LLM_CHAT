from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import Decision, safe_hold, validate_and_normalize
from .fallback import FallbackState, decide_fallback
from .providers import ModelProvider

SYSTEM_PROMPT = """
Ты контроллер NeuroSync и управляешь программатором.
Отвечай только JSON-объектом, без markdown и пояснений.

Честность: ты задаёшь только команды программатора (set_spec / set_timeline / stop / hold), не весь интерфейс приложения.
Не утверждай в assistant_reply, что звук «уже включён» или «я активировал», если исполнение зависит от клиента и пользователь не подтвердил применение.
Если запрос требует действий вне этого контура — hold и коротко объясни ограничение в assistant_reply по-русски.

Допустимые action: set_spec, set_timeline, hold, stop.
Если action=set_spec, поле spec обязательно и непустое.
Если action=set_timeline, поле timeline обязательно: строка с переносами \\n (каждая строка "<mm:ss|hh:mm:ss> <spec>") ИЛИ JSON-массив таких строк — оба варианта принимаются.
Пример timeline для белого шума 30 сек: "0:00 white/0.70\\n0:30 off"

Поддерживаемый язык spec:
- "<carrier>+<beat>/<amp>"      пример: "200+7/0.60"
- "<color>/<amp>"               color: white|pink|brown, пример: "white/0.70"
- "sweep:<f0>-><f1>/<dur>/<amp>" пример: "sweep:1000->100/30/0.6"
- "off"

Запрещены синонимы/псевдоязык вроде "white_noise, duration=30s".

Если пользователь просит включить шум/биение/сценарий на **конкретное число секунд** — верни **set_timeline** с метками `0:00 …` и `0:MM:SS off` (или `0:SS off` для секунд), без hold.

Если пользователь явно поручает **автономию** («свободное управление», «свободный полёт», «пробуй повлиять звуком», «настрой сам», «режим свободного полёта») —
верни **конкретный** `set_spec` или компактный `set_timeline` (не общие советы в hold): опирайся на метрики из промпта (attention/meditation и т.д.). reason_code например `operator_autopilot`.

Если запрос неоднозначен и **нет** ни тайминга, ни полномочий автономии — верни hold.

При action=hold, если пользователь проверяет связь («оператор», «на связи», приём) или ждёт короткого ответа без смены программы,
добавь поле assistant_reply — одно-два предложения по-русски (не markdown).
Пример: {"action":"hold","confidence":1,"reason_code":"liaison","assistant_reply":"На связи, канал открыт. Готов к командам программатора."}
"""


_RATE_LIMIT_WINDOW_FALLBACK_S = 60.0
_DEBOUNCE_EFFECTIVE_REPEAT_CAP = 10


@dataclass
class RuntimeState:
    last_sent_spec: str = ""
    last_sent_timeline: str = ""
    last_sent_at: float = 0.0
    fallback_state: FallbackState = field(default_factory=FallbackState)
    # LLM anti-flutter: same command must repeat N times (see apply_llm_debounce).
    debounce_spec_buf: str = ""
    debounce_spec_n: int = 0
    debounce_tl_buf: str = ""
    debounce_tl_n: int = 0
    # Limits successful set_spec / set_timeline commits per sliding window (see apply_llm_rate_limit).
    program_change_times: list[float] = field(default_factory=list)


def _post_json(url: str, obj: dict[str, Any], *, timeout_s: float = 3.0) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as _resp:
        pass


def send_decision(ui_agent_api_url: str, decision: Decision) -> None:
    if decision.action == "set_spec" and decision.spec:
        _post_json(ui_agent_api_url, {"topic": "program.set_spec", "payload": {"spec": decision.spec}})
    elif decision.action == "set_timeline" and decision.timeline:
        _post_json(
            ui_agent_api_url,
            {"topic": "program.set_timeline", "payload": {"timeline": decision.timeline}},
        )
    elif decision.action == "stop":
        _post_json(ui_agent_api_url, {"topic": "program.stop", "payload": {}})


def iter_observations(session_file: Path) -> Any:
    with session_file.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "observation":
                yield obj


def _build_user_prompt(obs: dict[str, Any], current_spec: str) -> str:
    compact = {
        "session_id": obs.get("session_id"),
        "t_monotonic_s": obs.get("t_monotonic_s"),
        "eeg": obs.get("eeg"),
        "hr": obs.get("hr"),
        "current_spec": current_spec,
    }
    return json.dumps(compact, ensure_ascii=False)


def decide(
    *,
    obs: dict[str, Any],
    state: RuntimeState,
    mode: str,
    provider: ModelProvider | None,
) -> Decision:
    if mode == "heuristic":
        return decide_fallback(obs, state.fallback_state)

    if provider is None:
        return safe_hold("provider_missing", source=mode)

    try:
        raw = provider.ask(SYSTEM_PROMPT, _build_user_prompt(obs, state.last_sent_spec))
        decision = validate_and_normalize(raw, source=mode)
    except Exception:
        decision = safe_hold("provider_error", source=mode)

    if decision.action == "hold":
        # Auto fallback if model fails repeatedly.
        fb = decide_fallback(obs, state.fallback_state)
        if decision.reason_code in {"invalid_json", "invalid_action", "provider_error"}:
            return Decision(
                action=fb.action,
                spec=fb.spec,
                confidence=fb.confidence,
                reason_code=f"{decision.reason_code}->{fb.reason_code}",
                source=f"{mode}+fallback",
                timeline=None,
                assistant_reply=None,
            )
    return decision


def reset_llm_debounce(state: RuntimeState) -> None:
    state.debounce_spec_buf = ""
    state.debounce_spec_n = 0
    state.debounce_tl_buf = ""
    state.debounce_tl_n = 0


def llm_debounce_repeat_required(mode: str) -> int:
    """For heuristic always 1 (no debounce). Env NSP_LLM_DEBOUNCE_MATCHES, default 2; 1 = off."""
    if mode == "heuristic":
        return 1
    raw = os.environ.get("NSP_LLM_DEBOUNCE_MATCHES", "2")
    try:
        v = int(str(raw).strip())
    except ValueError:
        v = 2
    return max(1, v)


def _parse_optional_float_env(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def llm_debounce_effective_repeat_required(mode: str, decision: Decision) -> int:
    """Base repeats from NSP_LLM_DEBOUNCE_MATCHES, adjusted by optional confidence env (local/cloud only)."""
    base = llm_debounce_repeat_required(mode)
    if base <= 1:
        return 1
    bypass = _parse_optional_float_env("NSP_LLM_DEBOUNCE_CONF_BYPASS")
    if bypass is not None:
        thr = max(0.0, min(1.0, bypass))
        if decision.confidence >= thr:
            return 1
    eff = base
    strict_lt = _parse_optional_float_env("NSP_LLM_DEBOUNCE_CONF_STRICT_LT")
    if strict_lt is not None:
        thr_lo = max(0.0, min(1.0, strict_lt))
        if decision.confidence < thr_lo:
            eff = min(base + 1, _DEBOUNCE_EFFECTIVE_REPEAT_CAP)
    return max(1, eff)


def llm_rate_limit_max_per_window() -> int:
    """NSP_LLM_RATE_LIMIT_PER_MIN: max program commits (spec+timeline) per window; 0 = off."""
    raw = os.environ.get("NSP_LLM_RATE_LIMIT_PER_MIN", "0")
    try:
        v = int(str(raw).strip())
    except ValueError:
        v = 0
    return max(0, v)


def llm_rate_limit_window_s() -> float:
    raw = os.environ.get("NSP_LLM_RATE_LIMIT_WINDOW_S", str(int(_RATE_LIMIT_WINDOW_FALLBACK_S)))
    try:
        w = float(str(raw).strip())
    except ValueError:
        w = _RATE_LIMIT_WINDOW_FALLBACK_S
    return max(1.0, w)


def _trim_program_change_times(state: RuntimeState, now: float) -> None:
    window = llm_rate_limit_window_s()
    state.program_change_times = [t for t in state.program_change_times if now - t < window]


def apply_llm_rate_limit(decision: Decision, state: RuntimeState) -> Decision:
    lim = llm_rate_limit_max_per_window()
    if lim <= 0:
        return decision
    if decision.action not in ("set_spec", "set_timeline"):
        return decision
    now = time.monotonic()
    _trim_program_change_times(state, now)
    if len(state.program_change_times) >= lim:
        return safe_hold("rate_limit", source=decision.source)
    return decision


def apply_decision_policy(
    decision: Decision,
    state: RuntimeState,
    *,
    mode: str,
    cooldown_s: float,
) -> Decision:
    """debounce → cooldown → rate_limit; сбрасывает streak debounce при входящем hold/stop."""
    if decision.action in ("hold", "stop"):
        reset_llm_debounce(state)
    d = apply_llm_debounce(decision, state, mode=mode)
    d = apply_cooldown(d, state, cooldown_s=cooldown_s)
    d = apply_llm_rate_limit(d, state)
    return d


def apply_llm_debounce(decision: Decision, state: RuntimeState, *, mode: str) -> Decision:
    req = llm_debounce_effective_repeat_required(mode, decision)
    if req <= 1:
        return decision
    if decision.action == "set_spec" and decision.spec:
        spec = decision.spec.strip()
        if spec == state.debounce_spec_buf:
            state.debounce_spec_n += 1
        else:
            state.debounce_spec_buf = spec
            state.debounce_spec_n = 1
        if state.debounce_spec_n < req:
            return safe_hold("debounce", source=decision.source)
        return decision
    if decision.action == "set_timeline" and decision.timeline:
        tl = decision.timeline.strip()
        if tl == state.debounce_tl_buf:
            state.debounce_tl_n += 1
        else:
            state.debounce_tl_buf = tl
            state.debounce_tl_n = 1
        if state.debounce_tl_n < req:
            return safe_hold("debounce_timeline", source=decision.source)
        return decision
    return decision


def apply_cooldown(decision: Decision, state: RuntimeState, *, cooldown_s: float) -> Decision:
    now = time.monotonic()
    if decision.action == "set_spec" and decision.spec:
        if decision.spec == state.last_sent_spec:
            return safe_hold("same_spec", source=decision.source)
        if now - state.last_sent_at < cooldown_s:
            return safe_hold("cooldown", source=decision.source)
        return decision
    if decision.action == "set_timeline" and decision.timeline:
        tl = decision.timeline.strip()
        if tl == state.last_sent_timeline.strip():
            return safe_hold("same_timeline", source=decision.source)
        if now - state.last_sent_at < cooldown_s:
            return safe_hold("cooldown", source=decision.source)
        return decision
    return decision


def commit_decision_state(decision: Decision, state: RuntimeState) -> None:
    reset_llm_debounce(state)
    if decision.action == "set_spec" and decision.spec:
        state.last_sent_spec = decision.spec
        state.last_sent_timeline = ""
        state.last_sent_at = time.monotonic()
        if llm_rate_limit_max_per_window() > 0:
            _trim_program_change_times(state, state.last_sent_at)
            state.program_change_times.append(state.last_sent_at)
    elif decision.action == "set_timeline" and decision.timeline:
        state.last_sent_timeline = decision.timeline.strip()
        state.last_sent_spec = ""
        state.last_sent_at = time.monotonic()
        if llm_rate_limit_max_per_window() > 0:
            _trim_program_change_times(state, state.last_sent_at)
            state.program_change_times.append(state.last_sent_at)
    elif decision.action == "stop":
        state.last_sent_at = time.monotonic()


def step_observation(
    obs: dict[str, Any],
    *,
    mode: str,
    provider: ModelProvider | None,
    state: RuntimeState,
    cooldown_s: float,
    ui_agent_api_url: str,
    send_actions: bool,
) -> dict[str, Any]:
    decision = decide(obs=obs, state=state, mode=mode, provider=provider)
    decision = apply_decision_policy(decision, state, mode=mode, cooldown_s=cooldown_s)
    if decision.action == "set_spec" and decision.spec:
        commit_decision_state(decision, state)
        if send_actions:
            send_decision(ui_agent_api_url, decision)
    elif decision.action == "set_timeline" and decision.timeline:
        commit_decision_state(decision, state)
        if send_actions:
            send_decision(ui_agent_api_url, decision)
    elif decision.action == "stop":
        commit_decision_state(decision, state)
        if send_actions:
            send_decision(ui_agent_api_url, decision)
    return {"t_monotonic_s": obs.get("t_monotonic_s"), **decision.to_dict()}
