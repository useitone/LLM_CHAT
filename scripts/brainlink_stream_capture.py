#!/usr/bin/env python3
"""
Capture raw BLE notify packets from BrainLink (Nordic UART).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

try:
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakDeviceNotFoundError
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: bleak.\n"
        f"  Python: {sys.executable}\n"
        "  Install: python -m pip install bleak"
    ) from exc

try:
    from neurosync_pro.eeg.ble_stream import (
        DEFAULT_NOTIFY_UUID,
        DEFAULT_WRITE_UUID,
        normalize_ble_address,
    )
except ImportError:
    # Script may run without `pip install -e .`; only bleak is required.
    DEFAULT_NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
    DEFAULT_WRITE_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

    def normalize_ble_address(address: str) -> str:
        a = address.strip().upper()
        if "-" in a:
            a = a.replace("-", ":")
        return a


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture BrainLink BLE notify stream to JSONL."
    )
    parser.add_argument(
        "--address",
        required=True,
        help="BLE MAC address, e.g. C0:E2:FC:2D:AC:10",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=30.0,
        help="Capture duration in seconds (default: 30).",
    )
    parser.add_argument(
        "--notify-uuid",
        default=DEFAULT_NOTIFY_UUID,
        help="Notify characteristic UUID.",
    )
    parser.add_argument(
        "--write-uuid",
        default=DEFAULT_WRITE_UUID,
        help="Write characteristic UUID for optional init command.",
    )
    parser.add_argument(
        "--init-hex",
        default="",
        help="Optional init command in hex, e.g. aa2101.",
    )
    parser.add_argument(
        "--output",
        default="docs/specs/brainlink-raw-capture.jsonl",
        help="Output JSONL path.",
    )
    parser.add_argument(
        "--scan-first",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="Scan this many seconds before connect; prints address/name (helps if connect fails).",
    )
    return parser.parse_args()


def to_packet_record(sender: Any, data: bytearray) -> dict[str, Any]:
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "sender": str(sender),
        "len": len(data),
        "hex": bytes(data).hex(),
    }


async def main() -> int:
    args = parse_args()
    address = normalize_ble_address(args.address)
    if address != args.address.strip():
        print(f"Using normalized address: {address}")

    if args.scan_first > 0:
        print(f"Scanning BLE for {args.scan_first:.1f}s…")
        devices = await BleakScanner.discover(timeout=args.scan_first)
        for d in sorted(devices, key=lambda x: (x.name or "", x.address)):
            rssi = getattr(d, "rssi", None)
            extra = f" rssi={rssi}" if rssi is not None else ""
            print(f"  {d.address}  name={d.name!r}{extra}")
        print()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    packet_count = 0

    try:
        with output_path.open("w", encoding="utf-8") as out:
            async with BleakClient(address) as client:
                if not client.is_connected:
                    raise RuntimeError(f"Failed to connect: {address}")

                print(f"Connected: {address}")
                print(f"Notify UUID: {args.notify_uuid}")
                print(f"Capture duration: {args.duration:.1f}s")

                def notification_handler(sender: Any, data: bytearray) -> None:
                    nonlocal packet_count
                    packet_count += 1
                    record = to_packet_record(sender, data)
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    out.flush()

                await client.start_notify(args.notify_uuid, notification_handler)
                print("Notify subscription started.")

                init_hex = args.init_hex.strip().replace(" ", "")
                if init_hex:
                    init_bytes = bytes.fromhex(init_hex)
                    await client.write_gatt_char(args.write_uuid, init_bytes, response=False)
                    print(f"Init command sent ({len(init_bytes)} bytes).")

                try:
                    await asyncio.sleep(args.duration)
                finally:
                    await client.stop_notify(args.notify_uuid)
                    print("Notify subscription stopped.")
    except BleakDeviceNotFoundError:
        print(
            f"BLE: устройство {address!r} не найдено стеком Windows.\n"
            "Проверьте: гарнитура включена и рядом; не подключена к телефону/другому ПК; "
            "Bluetooth в Windows включён.\n"
            "Попробуйте скан перед подключением:\n"
            f'  python scripts/brainlink_stream_capture.py --address "{address}" '
            f"--duration 10 --output {args.output} --scan-first 15\n"
            "или полный отчёт:\n"
            "  python scripts/brainlink_probe.py --scan-time 15",
            file=sys.stderr,
        )
        return 1

    print(f"Saved capture to: {output_path}")
    print(f"Packets captured: {packet_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))