"""Incremental BLE notify chunks → decoded EEG frames (state machine)."""

from __future__ import annotations

from neurosync_pro.eeg.protocol import BrainLinkStateMachineParser, EegFrameDecoded


class LiveEegDecoder:
    """Holds parser state across notify chunks (packets may split)."""

    def __init__(self) -> None:
        self._sm = BrainLinkStateMachineParser()

    def feed_chunk(self, data: bytes | bytearray) -> list[tuple[str, object]]:
        out: list[tuple[str, object]] = []
        for b in data:
            for kind, payload in self._sm.feed_byte(b):
                if kind == "eeg":
                    out.append(("eeg", payload))
                elif kind == "extend_raw":
                    out.append(("extend_raw", payload))
        return out
