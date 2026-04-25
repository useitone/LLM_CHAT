"""EEG acquisition and decoding (BrainLink Pro)."""

from neurosync_pro.eeg.ble_stream import (
    DEFAULT_NOTIFY_UUID,
    DEFAULT_WRITE_UUID,
    normalize_ble_address,
    run_ble_notify_session,
    schedule_stop,
)
from neurosync_pro.eeg.live_decode import LiveEegDecoder
from neurosync_pro.eeg.vendor_stream import Aabb0cHeartRateParser, try_parse_aabb0c_hr_payload
from neurosync_pro.eeg.protocol import (
    EegFrameDecoded,
    ExtendFrameDecoded,
    GyroFrameDecoded,
    ShortFrameDecoded,
    decode_eeg,
    decode_extend,
    decode_gyro,
    decode_short,
    extract_all_eeg_frames,
    run,
    scan_payload,
)

__all__ = [
    "DEFAULT_NOTIFY_UUID",
    "DEFAULT_WRITE_UUID",
    "normalize_ble_address",
    "EegFrameDecoded",
    "ExtendFrameDecoded",
    "GyroFrameDecoded",
    "ShortFrameDecoded",
    "decode_eeg",
    "decode_extend",
    "decode_gyro",
    "decode_short",
    "extract_all_eeg_frames",
    "LiveEegDecoder",
    "Aabb0cHeartRateParser",
    "try_parse_aabb0c_hr_payload",
    "run",
    "run_ble_notify_session",
    "scan_payload",
    "schedule_stop",
]