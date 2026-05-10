from neurosync_pro.eeg.hr_filter import HrMedianSmoother


def test_hr_median_smoother_rejects_out_of_range() -> None:
    s = HrMedianSmoother(window=5, bpm_min=40, bpm_max=180)
    assert s.feed(10) is None
    assert s.current() is None
    assert s.feed(60) == 60
    assert s.feed(181) is None
    assert s.current() == 60


def test_hr_median_smoother_median_over_window() -> None:
    s = HrMedianSmoother(window=5)
    for v in (60, 61, 200, 62, 63):
        s.feed(v)
    # sorted = [60,61,62,63,200] -> median 62
    assert s.current() == 62


def test_hr_step_limit_requires_confirmation_for_big_jump() -> None:
    s = HrMedianSmoother(window=1, max_delta_per_s=5.0, jump_confirm=2)
    assert s.feed(60, t=0.0) == 60
    # Big jump to 90 at t=1: allow=5 -> should hold 60 until confirmed
    assert s.feed(90, t=1.0) == 60
    assert s.feed(90, t=2.0) == 90

