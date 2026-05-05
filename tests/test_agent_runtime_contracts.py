from neurosync_pro.agent_runtime.contracts import validate_and_normalize


def test_validate_set_spec_ok() -> None:
    out = validate_and_normalize(
        '{"action":"set_spec","spec":"200+10/0.55 pink/0.06","confidence":0.8,"reason_code":"ok"}',
        source="local",
    )
    assert out.action == "set_spec"
    assert out.spec == "200+10/0.55 pink/0.06"
    assert out.confidence == 0.8
    assert out.source == "local"


def test_validate_invalid_json_becomes_hold() -> None:
    out = validate_and_normalize("not-json", source="cloud")
    assert out.action == "hold"
    assert out.reason_code == "invalid_json"


def test_validate_missing_spec_becomes_hold() -> None:
    out = validate_and_normalize('{"action":"set_spec","confidence":0.9}', source="local")
    assert out.action == "hold"
    assert out.reason_code == "missing_spec"


def test_validate_set_timeline_ok() -> None:
    raw = (
        '{"action":"set_timeline","timeline":"0:00 white/0.70\\n0:30 off",'
        '"confidence":0.9,"reason_code":"ok"}'
    )
    out = validate_and_normalize(raw, source="local")
    assert out.action == "set_timeline"
    assert "white/0.70" in (out.timeline or "")
    assert out.spec is None


def test_validate_set_timeline_accepts_json_array_of_lines() -> None:
    raw = (
        '{"action":"set_timeline","timeline":["0:00 200+5/0.5 brown/0.07","0:30 200+8/0.5 pink/0.06",'
        '"1:00 off"],"confidence":0.9,"reason_code":"ok"}'
    )
    out = validate_and_normalize(raw, source="local")
    assert out.action == "set_timeline"
    assert out.timeline is not None
    assert "0:00 200+5/0.5 brown/0.07" in out.timeline
    assert "0:30 200+8/0.5 pink/0.06" in out.timeline
    assert out.timeline.endswith("1:00 off")


def test_validate_stop_ok() -> None:
    out = validate_and_normalize('{"action":"stop","confidence":1,"reason_code":"x"}', source="local")
    assert out.action == "stop"
    assert out.spec is None
    assert out.timeline is None


def test_validate_hold_with_assistant_reply() -> None:
    raw = (
        '{"action":"hold","confidence":1,"reason_code":"liaison",'
        '"assistant_reply":"На связи."}'
    )
    out = validate_and_normalize(raw, source="local")
    assert out.action == "hold"
    assert out.assistant_reply == "На связи."


def test_validate_reply_alias_for_assistant_reply() -> None:
    out = validate_and_normalize(
        '{"action":"hold","confidence":1,"reason_code":"x","reply":"Приём."}',
        source="local",
    )
    assert out.assistant_reply == "Приём."


def test_validate_missing_timeline_becomes_hold() -> None:
    out = validate_and_normalize('{"action":"set_timeline","confidence":0.9}', source="local")
    assert out.action == "hold"
    assert out.reason_code == "missing_timeline"


def test_validate_rejects_unknown_spec_token() -> None:
    out = validate_and_normalize(
        '{"action":"set_spec","spec":"white_noise","confidence":1}',
        source="local",
    )
    assert out.action == "hold"
    assert out.reason_code.startswith("invalid_spec:")


def test_validate_rejects_bad_timeline_spec() -> None:
    raw = '{"action":"set_timeline","timeline":"0:00 not_a_real_token\\n0:30 off"}'
    out = validate_and_normalize(raw, source="local")
    assert out.action == "hold"
    assert out.reason_code.startswith("invalid_timeline:")
