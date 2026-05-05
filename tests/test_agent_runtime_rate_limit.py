import pytest

from neurosync_pro.agent_runtime.contracts import Decision
from neurosync_pro.agent_runtime.loop import RuntimeState, apply_llm_rate_limit, commit_decision_state


def _spec(spec: str) -> Decision:
    return Decision(
        action="set_spec",
        spec=spec,
        confidence=1.0,
        reason_code="t",
        source="local",
        timeline=None,
    )


def test_rate_limit_disabled_when_env_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LLM_RATE_LIMIT_PER_MIN", "0")
    st = RuntimeState()
    assert apply_llm_rate_limit(_spec("white/0.7"), st).action == "set_spec"


def test_rate_limit_blocks_third_commit_in_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LLM_RATE_LIMIT_PER_MIN", "2")
    t = [100.0]
    monkeypatch.setattr("neurosync_pro.agent_runtime.loop.time.monotonic", lambda: t[0])

    st = RuntimeState()
    d1 = _spec("white/0.7")
    assert apply_llm_rate_limit(d1, st).action == "set_spec"
    commit_decision_state(d1, st)

    d2 = _spec("pink/0.5")
    assert apply_llm_rate_limit(d2, st).action == "set_spec"
    commit_decision_state(d2, st)

    d3 = _spec("brown/0.6")
    r = apply_llm_rate_limit(d3, st)
    assert r.action == "hold"
    assert r.reason_code == "rate_limit"


def test_rate_limit_allows_after_window_slides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LLM_RATE_LIMIT_PER_MIN", "2")
    monkeypatch.setenv("NSP_LLM_RATE_LIMIT_WINDOW_S", "60")
    t = [100.0]
    monkeypatch.setattr("neurosync_pro.agent_runtime.loop.time.monotonic", lambda: t[0])

    st = RuntimeState()
    for spec in ("white/0.7", "pink/0.5"):
        d = _spec(spec)
        assert apply_llm_rate_limit(d, st).action == "set_spec"
        commit_decision_state(d, st)

    assert apply_llm_rate_limit(_spec("brown/0.6"), st).action == "hold"

    t[0] = 161.0
    assert apply_llm_rate_limit(_spec("brown/0.6"), st).action == "set_spec"
