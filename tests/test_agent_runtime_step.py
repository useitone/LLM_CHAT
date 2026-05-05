from neurosync_pro.agent_runtime.loop import RuntimeState, step_observation


def test_step_observation_heuristic_sets_spec() -> None:
    obs = {
        "type": "observation",
        "t_monotonic_s": 1.0,
        "eeg": {"attention": {"mean": 20}, "meditation": {"mean": 40}},
    }
    state = RuntimeState()
    row = step_observation(
        obs,
        mode="heuristic",
        provider=None,
        state=state,
        cooldown_s=0.0,
        ui_agent_api_url="http://127.0.0.1:9/v1/event",
        send_actions=False,
    )
    assert row["action"] == "set_spec"
    assert row["spec"]
    assert "brown" in row["spec"]
