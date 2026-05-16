"""ThinkGear-style byte parser for TGAM / serial streams (0xAA 0xAA length … checksum).

Separate from :class:`neurosync_pro.eeg.protocol.BrainLinkStateMachineParser`, which targets
BrainLink BLE notify framing (0x20 EEG container). Use this for raw serial captures when
frames match the classic TGAM packet layout (see ``docs/brain_master_parser.py`` reference).

Checksum: ``(~(sum(payload) & 0xFF)) & 0xFF`` — same as NeuroSky developer docs.
"""

from __future__ import annotations

from dataclasses import dataclass

SYNC = 0xAA
EXCODE = 0x55
CODE_RAW = 0x80
CODE_POOR_SIGNAL = 0x02
CODE_ATTENTION = 0x04
CODE_MEDITATION = 0x05
CODE_BLINK = 0x16


@dataclass(frozen=True)
class ThinkgearSerialFrame:
    """One decoded payload after successful checksum (serial TGAM semantics)."""

    poor_signal: int
    attention: int
    meditation: int
    blink: int


class ThinkgearSerialParser:
    """Incremental parser: feed bytes from UART; obtain frames via :meth:`feed_byte`."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._payload: list[int] = []
        self._payload_length = 0
        self._state = "SYNC"
        self.poor_signal = 200
        self.attention = 0
        self.meditation = 0
        self.blink = 0

    def feed_byte(self, b: int) -> ThinkgearSerialFrame | None:
        """Return a frame when a full packet checksum validates; otherwise ``None``."""

        byte = b & 0xFF

        if self._state == "SYNC":
            if byte == SYNC:
                self._state = "SYNC_CHECK"

        elif self._state == "SYNC_CHECK":
            if byte == SYNC:
                self._state = "PAYLOAD_LENGTH"
            else:
                self._state = "SYNC"

        elif self._state == "PAYLOAD_LENGTH":
            self._payload_length = byte
            self._payload = []
            if 0 < self._payload_length < 170:
                self._state = "PAYLOAD"
            else:
                self._state = "SYNC"

        elif self._state == "PAYLOAD":
            self._payload.append(byte)
            if len(self._payload) >= self._payload_length:
                self._state = "CHECKSUM"

        elif self._state == "CHECKSUM":
            received_checksum = byte
            calculated = (~(sum(self._payload) & 0xFF)) & 0xFF
            out: ThinkgearSerialFrame | None = None
            if calculated == received_checksum:
                self._decode_payload()
                out = ThinkgearSerialFrame(
                    poor_signal=int(self.poor_signal),
                    attention=int(self.attention),
                    meditation=int(self.meditation),
                    blink=int(self.blink),
                )
            self._state = "SYNC"
            return out

        return None

    def _decode_payload(self) -> None:
        i = 0
        p = self._payload
        while i < len(p):
            code = p[i]
            i += 1

            if code == EXCODE:
                continue

            if i >= len(p):
                break

            if code == CODE_POOR_SIGNAL:
                self.poor_signal = int(p[i])
                i += 1
            elif code == CODE_ATTENTION:
                self.attention = int(p[i])
                i += 1
            elif code == CODE_MEDITATION:
                self.meditation = int(p[i])
                i += 1
            elif code == CODE_BLINK:
                self.blink = int(p[i])
                i += 1
            elif code == CODE_RAW:
                # Most TGAM streams use a 16-bit big-endian sample here.
                if i + 1 < len(p):
                    i += 2
                else:
                    i += 1
            else:
                if code >= 0x80 and i < len(p):
                    ext_len = int(p[i])
                    i += 1 + min(ext_len, max(0, len(p) - i))
                else:
                    i += 1
