import pytest

from neurosync_pro.agent_runtime.contracts import Decision
from neurosync_pro.agent_runtime.loop import RuntimeState, apply_llm_debounce


def _spec_decision(spec: str, *, confidence: float = 1.0) -> Decision:
    return Decision(
        action="set_spec",
        spec=spec,
        confidence=confidence,
        reason_code="t",
        source="local",
        timeline=None,
    )


def test_llm_debounce_requires_repeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LLM_DEBOUNCE_MATCHES", "2")
    st = RuntimeState()
    d = _spec_decision("white/0.7")
    assert apply_llm_debounce(d, st, mode="local").action == "hold"
    assert apply_llm_debounce(d, st, mode="local").action == "set_spec"


def test_heuristic_skips_debounce(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LLM_DEBOUNCE_MATCHES", "5")
    st = RuntimeState()
    d = _spec_decision("white/0.7")
    assert apply_llm_debounce(d, st, mode="heuristic").action == "set_spec"


def test_debounce_disabled_when_env_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LLM_DEBOUNCE_MATCHES", "1")
    st = RuntimeState()
    d = _spec_decision("white/0.7")
    assert apply_llm_debounce(d, st, mode="local").action == "set_spec"


def test_confidence_bypass_reduces_repeats(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LLM_DEBOUNCE_MATCHES", "2")
    monkeypatch.setenv("NSP_LLM_DEBOUNCE_CONF_BYPASS", "0.85")
    st = RuntimeState()
    d = _spec_decision("white/0.7", confidence=0.9)
    assert apply_llm_debounce(d, st, mode="local").action == "set_spec"


def test_confidence_bypass_off_when_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LLM_DEBOUNCE_MATCHES", "2")
    monkeypatch.setenv("NSP_LLM_DEBOUNCE_CONF_BYPASS", "0.95")
    st = RuntimeState()
    d = _spec_decision("white/0.7", confidence=0.5)
    assert apply_llm_debounce(d, st, mode="local").action == "hold"
    assert apply_llm_debounce(d, st, mode="local").action == "set_spec"


def test_confidence_strict_adds_one_repeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LLM_DEBOUNCE_MATCHES", "2")
    monkeypatch.setenv("NSP_LLM_DEBOUNCE_CONF_STRICT_LT", "0.9")
    monkeypatch.delenv("NSP_LLM_DEBOUNCE_CONF_BYPASS", raising=False)
    st = RuntimeState()
    d = _spec_decision("white/0.7", confidence=0.4)
    assert apply_llm_debounce(d, st, mode="local").action == "hold"
    assert apply_llm_debounce(d, st, mode="local").action == "hold"
    assert apply_llm_debounce(d, st, mode="local").action == "set_spec"
