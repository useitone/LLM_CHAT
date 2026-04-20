#!/usr/bin/env python3
"""
Run Macrotellect COM JSONL and BLE raw JSONL capture for the same wall-clock window
(overlapping UTC timestamps) for scripts/brainlink_compare_macrotellect_ble.py.

COM runs in a thread; BLE runs on asyncio. Same duration for both.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, TextIO

try:
    import serial
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Install pyserial: pip install -e .") from exc

try:
    from bleak import BleakClient
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Install bleak: pip install bleak") from exc

DEFAULT_PYD_DIR = Path(__file__).resolve().parent.parent / "docs/specs/vendor/macrotellect_brainlink_parser"
DEFAULT_NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
DEFAULT_WRITE_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"


def load_brain_link_parser_class(pyd_dir: Path) -> type:
    pyd_file = pyd_dir / "BrainLinkParser.pyd"
    if not pyd_file.is_file():
        raise SystemExit(
            f"BrainLinkParser.pyd not found at:\n  {pyd_file}\n"
            "See docs/specs/vendor/macrotellect_brainlink_parser/README.md"
        )
    if str(pyd_dir.resolve()) not in sys.path:
        sys.path.insert(0, str(pyd_dir.resolve()))
    try:
        from BrainLinkParser import BrainLinkParser  # type: ignore[import-not-found]
    except ImportError as e:
        raise SystemExit(
            f"Failed to import BrainLinkParser from {pyd_dir}: {e}\n"
            "Match Python version/bitness to the .pyd."
        ) from e
    return BrainLinkParser


def _eeg_dict(obj: Any) -> dict[str, Any]:
    keys = (
        "signal",
        "attention",
        "meditation",
        "delta",
        "theta",
        "lowAlpha",
        "highAlpha",
        "lowBeta",
        "highBeta",
        "lowGamma",
        "highGamma",
    )
    return {k: int(getattr(obj, k, 0)) for k in keys if hasattr(obj, k)}


def _extend_dict(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in ("ap", "battery", "version", "gnaw", "temperature", "heart"):
        if hasattr(obj, k):
            v = getattr(obj, k)
            if isinstance(v, (int, float, str)) or v is None:
                out[k] = v
            else:
                out[k] = str(v)
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Concurrent COM (Macrotellect) + BLE capture for timestamp-aligned compare."
    )
    p.add_argument("--address", required=True, help="BLE MAC, e.g. C0:E2:FC:2D:AC:10")
    p.add_argument("--port", default="COM3", help="Virtual COM for BrainLink (default COM3).")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--duration", type=float, default=30.0, help="Seconds (default 30).")
    p.add_argument(
        "--stem",
        type=Path,
        default=Path("docs/specs/brainlink-concurrent-session"),
        help="Output prefix: <stem>-macrotellect.jsonl, <stem>-ble.jsonl, <stem>-session.json",
    )
    p.add_argument("--pyd-dir", type=Path, default=DEFAULT_PYD_DIR)
    p.add_argument("--notify-uuid", default=DEFAULT_NOTIFY_UUID)
    p.add_argument("--write-uuid", default=DEFAULT_WRITE_UUID)
    p.add_argument("--init-hex", default="", help="Optional BLE init hex, e.g. aa2101")
    p.add_argument("--read-size", type=int, default=4096)
    return p.parse_args()


def to_ble_record(sender: Any, data: bytearray) -> dict[str, Any]:
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "sender": str(sender),
        "len": len(data),
        "hex": bytes(data).hex(),
    }


def run_com_loop(
    port: str,
    baud: int,
    read_size: int,
    pyd_dir: Path,
    out_path: Path,
    end_monotonic: float,
    err: list[str],
) -> None:
    BrainLinkParser = load_brain_link_parser_class(pyd_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_file: TextIO = out_path.open("w", encoding="utf-8")

    def write_event(kind: str, payload: dict[str, Any]) -> None:
        rec = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "type": kind,
            **payload,
        }
        out_file.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out_file.flush()

    def on_eeg(data: Any) -> None:
        write_event("eeg", {"eeg": _eeg_dict(data)})

    def on_extend(data: Any) -> None:
        write_event("extend", {"extend": _extend_dict(data)})

    def on_gyro(x: int, y: int, z: int) -> None:
        write_event("gyro", {"x": x, "y": y, "z": z})

    def on_rr(rr1: int, rr2: int, rr3: int) -> None:
        write_event("rr", {"rr1": rr1, "rr2": rr2, "rr3": rr3})

    def on_raw(raw: int) -> None:
        write_event("raw", {"raw": int(raw)})

    parser = BrainLinkParser(on_eeg, on_extend, on_gyro, on_rr, on_raw)
    try:
        ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.25,
        )
    except serial.SerialException as e:
        err.append(str(e))
        out_file.close()
        return

    try:
        while time.monotonic() < end_monotonic:
            chunk = ser.read(read_size)
            if chunk:
                parser.parse(bytes(chunk))
    finally:
        ser.close()
        out_file.close()


async def run_ble_loop(
    address: str,
    notify_uuid: str,
    write_uuid: str,
    init_hex: str,
    out_path: Path,
    end_monotonic: float,
    err: list[str],
) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    try:
        with out_path.open("w", encoding="utf-8") as out:
            async with BleakClient(address) as client:
                if not client.is_connected:
                    err.append(f"BLE connect failed: {address}")
                    return 0

                def notification_handler(sender: Any, data: bytearray) -> None:
                    nonlocal count
                    count += 1
                    out.write(
                        json.dumps(to_ble_record(sender, data), ensure_ascii=False) + "\n"
                    )
                    out.flush()

                await client.start_notify(notify_uuid, notification_handler)
                ih = init_hex.strip().replace(" ", "")
                if ih:
                    await client.write_gatt_char(
                        write_uuid, bytes.fromhex(ih), response=False
                    )
                while time.monotonic() < end_monotonic:
                    await asyncio.sleep(0.05)
                await client.stop_notify(notify_uuid)
    except Exception as e:  # pragma: no cover
        err.append(f"BLE: {e}")
    return count


async def async_main(args: argparse.Namespace) -> int:
    stem = args.stem
    stem.parent.mkdir(parents=True, exist_ok=True)
    com_out = Path(str(stem) + "-macrotellect.jsonl")
    ble_out = Path(str(stem) + "-ble.jsonl")
    session_out = Path(str(stem) + "-session.json")

    session_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    t0 = time.monotonic()
    end = t0 + args.duration
    com_err: list[str] = []
    ble_err: list[str] = []

    com_thread = threading.Thread(
        target=run_com_loop,
        kwargs={
            "port": args.port,
            "baud": args.baud,
            "read_size": args.read_size,
            "pyd_dir": args.pyd_dir,
            "out_path": com_out,
            "end_monotonic": end,
            "err": com_err,
        },
        daemon=True,
    )
    com_thread.start()
    n_ble = await run_ble_loop(
        args.address,
        args.notify_uuid,
        args.write_uuid,
        args.init_hex,
        ble_out,
        end,
        ble_err,
    )
    com_thread.join(timeout=args.duration + 5.0)

    meta = {
        "session_id": session_id,
        "duration_sec": args.duration,
        "started_utc": datetime.fromtimestamp(t0, UTC).isoformat(),
        "com_jsonl": str(com_out.resolve()),
        "ble_jsonl": str(ble_out.resolve()),
        "ble_packets": n_ble,
        "com_errors": com_err,
        "ble_errors": ble_err,
        "compare_hint": (
            "python scripts/brainlink_compare_macrotellect_ble.py "
            f"--macrotellect {com_out} --ble-raw {ble_out}"
        ),
    }
    session_out.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print("Session:", session_out, file=sys.stderr)
    print("COM:", com_out, file=sys.stderr)
    print("BLE:", ble_out, "packets:", n_ble, file=sys.stderr)
    if com_err:
        print("COM errors:", com_err, file=sys.stderr)
    if ble_err:
        print("BLE errors:", ble_err, file=sys.stderr)
    return 1 if (com_err or ble_err) else 0


def main() -> int:
    args = parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
