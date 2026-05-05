from neurosync_pro.agent_runtime.spec_validate import validate_prog_spec, validate_timeline_body


def test_validate_prog_spec_binaural_and_noise() -> None:
    ok, err = validate_prog_spec("200+7/0.60 pink/0.08")
    assert ok and err == ""


def test_validate_prog_spec_off() -> None:
    ok, err = validate_prog_spec("off")
    assert ok and err == ""


def test_validate_prog_spec_rejects_garbage() -> None:
    ok, err = validate_prog_spec("white_noise")
    assert not ok
    assert "unknown_token" in err


def test_validate_timeline_ok() -> None:
    ok, err = validate_timeline_body("0:00 white/0.70\n0:30 off\n")
    assert ok and err == ""


def test_validate_timeline_bad_spec() -> None:
    ok, err = validate_timeline_body("0:00 white_noise\n")
    assert not ok
