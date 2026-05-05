import time

from neurosync_pro.agent_runtime.fallback import FallbackState, decide_fallback
from neurosync_pro.agent_runtime.loop import RuntimeState, apply_cooldown


def _obs(attention: float, meditation: float) -> dict:
    return {
        "type": "observation",
        "eeg": {
            "attention": {"mean": attention},
            "meditation": {"mean": meditation},
        },
    }


def test_fallback_changes_spec_for_low_attention() -> None:
    state = FallbackState()
    out = decide_fallback(_obs(20, 40), state)
    assert out.action == "set_spec"
    assert out.spec is not None
    assert "brown" in out.spec


def test_fallback_holds_on_same_spec() -> None:
    state = FallbackState()
    first = decide_fallback(_obs(55, 55), state)
    second = decide_fallback(_obs(56, 54), state)
    assert first.action == "set_spec"
    assert second.action == "hold"


def test_cooldown_blocks_fast_reapply() -> None:
    state = RuntimeState(last_sent_spec="200+6/0.50 pink/0.04", last_sent_at=time.monotonic())
    decision = decide_fallback(_obs(20, 20), state.fallback_state)
    blocked = apply_cooldown(decision, state, cooldown_s=1000.0)
    assert blocked.action == "hold"
