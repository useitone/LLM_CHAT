"""
BrainLink UART frame decode and JSONL scan (shared by CLI and scripts).

Layout aligned with docs/specs/pybrainlink-0.3.0/pybrainlink/protocol_parser.py.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

HEADER = bytes([0xAA, 0xAA])
TYPE_SHORT = bytes([0x04, 0x80, 0x02])
END = bytes([0x23, 0x23])


@dataclass
class ShortFrameDecoded:
    raw_hex: str
    value_be_signed: int
    byte_before_footer: int
    checksum_xor_2_to_6: int | None
    checksum_xor_2_to_7_match: bool
    checksum_sum_2_to_6_mod256: int | None
    checksum_sum_2_to_6_match: bool


@dataclass
class EegFrameDecoded:
    raw_hex: str
    signal_quality: int | None
    attention: int
    meditation: int
    delta: int
    theta: int
    low_alpha: int
    high_alpha: int
    low_beta: int
    high_beta: int
    low_gamma: int
    high_gamma: int


@dataclass
class GyroFrameDecoded:
    raw_hex: str
    x: int
    y: int
    z: int
    extra_int16: int | None
    wire_len: int


@dataclass
class ExtendFrameDecoded:
    raw_hex: str
    ap: int
    electric: int
    version: str
    temperature: float
    heart_rate: int


def decode_short(frame: bytes) -> ShortFrameDecoded:
    value = int.from_bytes(frame[5:7], "big", signed=True)
    b7 = frame[7]
    body = frame[2:7]
    xor_c = 0
    for b in body:
        xor_c ^= b
    sum_c = sum(body) & 0xFF
    return ShortFrameDecoded(
        raw_hex=frame.hex(),
        value_be_signed=value,
        byte_before_footer=b7,
        checksum_xor_2_to_6=xor_c,
        checksum_xor_2_to_7_match=(xor_c == b7),
        checksum_sum_2_to_6_mod256=sum_c,
        checksum_sum_2_to_6_match=(sum_c == b7),
    )


def decode_eeg(frame: bytes) -> EegFrameDecoded | None:
    """
    Legacy fixed-layout EEG decode.

    NOTE: BrainLink EEG payload is TLV-coded (ThinkGear-like) in practice; prefer
    the state-machine parser below (`BrainLinkStateMachineParser`) for BLE captures.
    """
    if len(frame) < 50 or frame[0:2] != HEADER or frame[2] != 0x20:
        return None
    # Keep compatibility with previous heuristic: many samples start with 0x02.
    if len(frame) > 3 and frame[3] != 0x02:
        return None
    if frame[-2:] != END:
        return None
    return None


class ParserState(Enum):
    SYNC = 1
    SYNC_CHECK = 2
    PAYLOAD_LENGTH = 3
    EEG_PAYLOAD = 4
    EEG_POST = 5
    RAW_PAYLOAD = 6
    EXTEND_PAYLOAD = 7
    GYRO_PAYLOAD = 8


class BrainLinkStateMachineParser:
    """
    Byte-by-byte parser aligned with haqury/pybrainlink C#-style state machine.

    This is the recommended parser for BLE raw JSONL.
    """

    SYNC_BYTE = 0xAA
    PAYLOAD_LENGTH_BYTE = 0x20  # EEG container
    RAW_LENGTH_BYTE = 0x04
    GYRO_LENGTH_BYTE = 0x07
    SIGNAL_CHECK_BYTE = 0x02
    EEG_CHECK_BYTE = 0x83
    EEG_LENGTH_BYTE = 0x18  # 24 bytes = 8 * 3-byte powers
    ATT_CHECK_BYTE = 0x04
    MED_CHECK_BYTE = 0x05
    AP_CHECK_BYTE = 0x06
    FLAG_CHECK_BYTE = 0x55  # end marker for extend

    def __init__(self) -> None:
        self.state = ParserState.SYNC
        self.payload = bytearray(128)
        self.offset = 0
        self.checksum = 0

    @staticmethod
    def _checksum(payload32: bytearray) -> int:
        s = 0
        for i in range(32):
            s += payload32[i]
        return (~s) & 0xFF

    @staticmethod
    def _get_eeg_power(payload: bytearray, idx: int) -> int:
        return int.from_bytes(payload[idx : idx + 3], "big")

    def _parse_eeg_payload(self) -> EegFrameDecoded | None:
        if self._checksum(self.payload) != self.checksum:
            return None

        signal_quality: int | None = None
        attention = 0
        meditation = 0
        delta = theta = 0
        low_alpha = high_alpha = 0
        low_beta = high_beta = 0
        low_gamma = high_gamma = 0

        idx = 0
        while idx < 32:
            code = self.payload[idx]
            idx += 1

            if code == self.SIGNAL_CHECK_BYTE:
                if idx < 32:
                    signal_quality = int(self.payload[idx])
                idx += 1
            elif code == self.EEG_CHECK_BYTE:
                if idx >= 32:
                    break
                length = self.payload[idx]
                idx += 1
                if length == self.EEG_LENGTH_BYTE and idx + 24 <= 32:
                    delta = self._get_eeg_power(self.payload, idx)
                    idx += 3
                    theta = self._get_eeg_power(self.payload, idx)
                    idx += 3
                    low_alpha = self._get_eeg_power(self.payload, idx)
                    idx += 3
                    high_alpha = self._get_eeg_power(self.payload, idx)
                    idx += 3
                    low_beta = self._get_eeg_power(self.payload, idx)
                    idx += 3
                    high_beta = self._get_eeg_power(self.payload, idx)
                    idx += 3
                    low_gamma = self._get_eeg_power(self.payload, idx)
                    idx += 3
                    high_gamma = self._get_eeg_power(self.payload, idx)
                    idx += 3
                else:
                    idx += length
            elif code == self.ATT_CHECK_BYTE:
                if idx < 32:
                    attention = int(self.payload[idx])
                idx += 1
            elif code == self.MED_CHECK_BYTE:
                if idx < 32:
                    meditation = int(self.payload[idx])
                idx += 1
            else:
                # Unknown code: cannot safely advance; stop.
                break

        return EegFrameDecoded(
            raw_hex=bytes(self.payload[:32]).hex(),
            signal_quality=signal_quality,
            attention=attention,
            meditation=meditation,
            delta=delta,
            theta=theta,
            low_alpha=low_alpha,
            high_alpha=high_alpha,
            low_beta=low_beta,
            high_beta=high_beta,
            low_gamma=low_gamma,
            high_gamma=high_gamma,
        )

    def feed_byte(self, b: int) -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []

        if self.state == ParserState.SYNC:
            if b == self.SYNC_BYTE:
                self.state = ParserState.SYNC_CHECK

        elif self.state == ParserState.SYNC_CHECK:
            if b == self.SYNC_BYTE:
                self.state = ParserState.PAYLOAD_LENGTH
            else:
                self.state = ParserState.SYNC

        elif self.state == ParserState.PAYLOAD_LENGTH:
            self.offset = 0
            if b == self.PAYLOAD_LENGTH_BYTE:
                self.state = ParserState.EEG_PAYLOAD
            elif b == self.RAW_LENGTH_BYTE:
                self.state = ParserState.RAW_PAYLOAD
            elif b == self.GYRO_LENGTH_BYTE:
                self.state = ParserState.GYRO_PAYLOAD
            else:
                self.state = ParserState.SYNC

        elif self.state == ParserState.EEG_PAYLOAD:
            self.payload[self.offset] = b
            self.offset += 1
            if self.offset > 32:
                self.checksum = b
                dec = self._parse_eeg_payload()
                if dec is not None:
                    out.append(("eeg", dec))
                self.state = ParserState.EEG_POST

        elif self.state == ParserState.EEG_POST:
            if b == self.AP_CHECK_BYTE:
                self.state = ParserState.EXTEND_PAYLOAD
                self.offset = 1
                self.payload[0] = self.AP_CHECK_BYTE
            elif b == self.SYNC_BYTE:
                self.state = ParserState.SYNC_CHECK
            else:
                self.state = ParserState.SYNC

        elif self.state == ParserState.RAW_PAYLOAD:
            # 4 bytes + checksum; we don't need raw here
            self.offset += 1
            if self.offset > 4:
                self.state = ParserState.SYNC

        elif self.state == ParserState.GYRO_PAYLOAD:
            # 8 bytes total (type + 6 bytes + checksum) per pybrainlink
            self.payload[self.offset] = b
            self.offset += 1
            if self.offset > 7:
                # gyro decode optional: x,y,z from payload[1:7]
                try:
                    x = int.from_bytes(self.payload[1:3], "big", signed=True)
                    y = int.from_bytes(self.payload[3:5], "big", signed=True)
                    z = int.from_bytes(self.payload[5:7], "big", signed=True)
                    out.append(("gyro", (x, y, z)))
                except Exception:
                    pass
                self.state = ParserState.SYNC

        elif self.state == ParserState.EXTEND_PAYLOAD:
            self.payload[self.offset] = b
            self.offset += 1
            if b == self.FLAG_CHECK_BYTE:
                # extend decode skipped for now
                self.state = ParserState.SYNC

        return out


def decode_gyro(frame: bytes) -> GyroFrameDecoded | None:
    if len(frame) < 10 or frame[0:2] != HEADER or frame[2] != 0x07 or frame[3] != 0x03:
        return None
    try:
        x = int.from_bytes(frame[4:6], "big", signed=True)
        y = int.from_bytes(frame[6:8], "big", signed=True)
        z = int.from_bytes(frame[8:10], "big", signed=True)
        extra: int | None = None
        if len(frame) >= 14 and frame[12:14] == END:
            extra = int.from_bytes(frame[10:12], "big", signed=True)
        return GyroFrameDecoded(
            raw_hex=frame.hex(),
            x=x,
            y=y,
            z=z,
            extra_int16=extra,
            wire_len=len(frame),
        )
    except (IndexError, ValueError):
        return None


def decode_extend(frame: bytes) -> ExtendFrameDecoded | None:
    if len(frame) < 15 or frame[0:2] != HEADER:
        return None
    if frame[2:5] != bytes([0xBB, 0x0C, 0x02]):
        return None
    data = frame[5:15]
    try:
        ap = data[0]
        electric = int.from_bytes(data[1:3], "big") if len(data) > 2 else 0
        version = f"{data[3]}.{data[4]}.{data[5]}" if len(data) > 5 else "0.0.0"
        temp_raw = int.from_bytes(data[6:8], "big") if len(data) > 7 else 0
        temperature = temp_raw / 10.0
        heart_rate = data[8] if len(data) > 8 else 0
        return ExtendFrameDecoded(
            raw_hex=frame[:15].hex(),
            ap=ap,
            electric=electric,
            version=version,
            temperature=temperature,
            heart_rate=heart_rate,
        )
    except (IndexError, ValueError):
        return None


def scan_payload(
    buf: bytes,
    stats: dict[str, Any],
    packet_timestamp_utc: str | None = None,
) -> None:
    """
    Scan a transport chunk.

    For BLE JSONL, each line contains a notify chunk which may include multiple
    packets and may split packets. We therefore:
      1) feed *every* byte into a state-machine parser (ThinkGear-like TLV)
      2) optionally also scan for fixed short/extend/gyro frames for diagnostics
    """
    sm: BrainLinkStateMachineParser = stats.setdefault("_sm_parser", BrainLinkStateMachineParser())
    xor_match = stats.setdefault("_xor_match", 0)
    sum_match = stats.setdefault("_sum_match", 0)

    # 1) State-machine pass (do not skip any bytes).
    for b in buf:
        for kind, payload in sm.feed_byte(b):
            if kind == "eeg":
                stats["eeg_count"] += 1
                if len(stats["eeg_samples"]) < stats["max_samples"]:
                    row = asdict(payload)
                    if packet_timestamp_utc is not None:
                        row["source_timestamp_utc"] = packet_timestamp_utc
                    stats["eeg_samples"].append(row)
            elif kind == "gyro":
                stats["gyro_count"] += 1

    # 2) Signature-based scan for short/extend/gyro (optional samples).
    i = 0
    n = len(buf)
    while i < n - 1:
        if buf[i : i + 2] != HEADER:
            i += 1
            continue

        if (
            i + 10 <= n
            and buf[i + 2 : i + 5] == TYPE_SHORT
            and buf[i + 8 : i + 10] == END
        ):
            frame = buf[i : i + 10]
            dec = decode_short(frame)
            stats["short_count"] += 1
            xor_match += int(dec.checksum_xor_2_to_7_match)
            sum_match += int(dec.checksum_sum_2_to_6_match)
            if len(stats["short_samples"]) < stats["max_samples"]:
                stats["short_samples"].append(asdict(dec))
            i += 10
            continue

        if (
            i + 14 <= n
            and buf[i + 2] == 0x07
            and buf[i + 3] == 0x03
            and buf[i + 12 : i + 14] == END
        ):
            frame = buf[i : i + 14]
            dec = decode_gyro(frame)
            if dec:
                stats["gyro_count"] += 1
                if len(stats["gyro_samples"]) < stats["max_samples"]:
                    stats["gyro_samples"].append(asdict(dec))
            i += 14
            continue

        if i + 10 <= n and buf[i + 2] == 0x07 and buf[i + 3] == 0x03:
            frame = buf[i : i + 10]
            dec = decode_gyro(frame)
            if dec:
                stats["gyro_count"] += 1
                if len(stats["gyro_samples"]) < stats["max_samples"]:
                    stats["gyro_samples"].append(asdict(dec))
            i += 10
            continue

        if i + 15 <= n and buf[i + 2 : i + 5] == bytes([0xBB, 0x0C, 0x02]):
            frame = buf[i : i + 15]
            dec = decode_extend(frame)
            if dec:
                stats["extend_count"] += 1
                if len(stats["extend_samples"]) < stats["max_samples"]:
                    stats["extend_samples"].append(asdict(dec))
            i += 15
            continue

        stats["skipped_at_aa"] += 1
        i += 1

    stats["_xor_match"] = xor_match
    stats["_sum_match"] = sum_match


def run(input_path: Path, max_samples: int) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "eeg_count": 0,
        "short_count": 0,
        "gyro_count": 0,
        "extend_count": 0,
        "skipped_at_aa": 0,
        "eeg_samples": [],
        "short_samples": [],
        "gyro_samples": [],
        "extend_samples": [],
        "max_samples": max_samples,
        "lines": 0,
    }

    with input_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            stats["lines"] += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            hx = rec.get("hex", "")
            if not hx:
                continue
            try:
                payload = bytes.fromhex(hx)
            except ValueError:
                continue
            scan_payload(payload, stats, packet_timestamp_utc=None)

    short_n = stats["short_count"]
    xor_m = stats.pop("_xor_match", 0)
    sum_m = stats.pop("_sum_match", 0)
    stats.pop("_sm_parser", None)
    stats["checksum_hypothesis"] = {
        "xor_bytes_2_to_6_equals_byte7": (
            round(xor_m / short_n, 4) if short_n else None
        ),
        "sum_bytes_2_to_6_mod256_equals_byte7": (
            round(sum_m / short_n, 4) if short_n else None
        ),
    }
    del stats["max_samples"]

    return stats


def extract_all_eeg_frames(input_path: Path) -> list[dict[str, Any]]:
    """Decode every EEG (50-byte) frame from a raw JSONL capture (no sample cap)."""
    stats: dict[str, Any] = {
        "eeg_count": 0,
        "short_count": 0,
        "gyro_count": 0,
        "extend_count": 0,
        "skipped_at_aa": 0,
        "eeg_samples": [],
        "short_samples": [],
        "gyro_samples": [],
        "extend_samples": [],
        "max_samples": 10**9,
        "lines": 0,
    }
    with input_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            stats["lines"] += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            hx = rec.get("hex", "")
            if not hx:
                continue
            try:
                payload = bytes.fromhex(hx)
            except ValueError:
                continue
            scan_payload(
                payload,
                stats,
                packet_timestamp_utc=rec.get("timestamp_utc"),
            )
    stats.pop("_xor_match", None)
    stats.pop("_sum_match", None)
    stats.pop("_sm_parser", None)
    return list(stats["eeg_samples"])
