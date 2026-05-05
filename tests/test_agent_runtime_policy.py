"""apply_decision_policy bundles debounce → cooldown → rate_limit."""

import pytest

from neurosync_pro.agent_runtime.contracts import Decision
from neurosync_pro.agent_runtime.loop import RuntimeState, apply_decision_policy


def _spec(spec: str, *, confidence: float = 1.0) -> Decision:
    return Decision(
        action="set_spec",
        spec=spec,
        confidence=confidence,
        reason_code="t",
        source="local",
        timeline=None,
    )


def test_policy_hold_resets_debounce_streak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LLM_DEBOUNCE_MATCHES", "2")
    monkeypatch.delenv("NSP_LLM_DEBOUNCE_CONF_BYPASS", raising=False)
    monkeypatch.delenv("NSP_LLM_DEBOUNCE_CONF_STRICT_LT", raising=False)
    st = RuntimeState()
    d0 = _spec("white/0.7")
    assert apply_decision_policy(d0, st, mode="local", cooldown_s=0.0).action == "hold"

    hold_d = Decision(
        action="hold",
        spec=None,
        confidence=0.0,
        reason_code="model",
        source="local",
        timeline=None,
    )
    apply_decision_policy(hold_d, st, mode="local", cooldown_s=0.0)

    d1 = _spec("pink/0.5")
    assert apply_decision_policy(d1, st, mode="local", cooldown_s=0.0).action == "hold"
    assert apply_decision_policy(d1, st, mode="local", cooldown_s=0.0).action == "set_spec"


def test_policy_matches_step_observation_gate_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cooldown should see debounce-passed decision (same as runtime chain)."""
    monkeypatch.setenv("NSP_LLM_DEBOUNCE_MATCHES", "1")
    monkeypatch.setenv("NSP_LLM_RATE_LIMIT_PER_MIN", "0")
    now = 1000.0
    monkeypatch.setattr("neurosync_pro.agent_runtime.loop.time.monotonic", lambda: now)
    st = RuntimeState()
    st.last_sent_spec = "off"
    st.last_sent_at = now
    d = _spec("white/0.7")
    out = apply_decision_policy(d, st, mode="local", cooldown_s=999.0)
    assert out.action == "hold"
    assert out.reason_code == "cooldown"
