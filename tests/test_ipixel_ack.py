"""Unit tests for iPIXEL notify ACK parsing."""

from __future__ import annotations

from neurosync_pro.light.ipixel_ack import IpixelAckManager


def test_ack_window_code_0() -> None:
    m = IpixelAckManager()
    h = m.make_notify_handler()
    m.reset()
    h(None, bytes([0x05, 0, 0, 0, 0]))
    assert m.window_event.is_set()


def test_ack_window_code_1() -> None:
    m = IpixelAckManager()
    h = m.make_notify_handler()
    m.reset()
    h(None, bytes([0x05, 0, 0, 0, 1]))
    assert m.window_event.is_set()


def test_ack_final_code_3_sets_both() -> None:
    m = IpixelAckManager()
    h = m.make_notify_handler()
    m.reset()
    h(None, bytes([0x05, 0, 0, 0, 3]))
    assert m.window_event.is_set()
    assert m.all_event.is_set()
