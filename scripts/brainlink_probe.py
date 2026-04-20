#!/usr/bin/env python3
"""
Brainlink BLE probe utility for Windows MVP preparation.

What it does:
1) Scans nearby BLE devices.
2) Filters probable BrainLink devices by name.
3) Optionally connects to a chosen device and exports GATT services/characteristics.
4) Saves all collected data into JSON for protocol notes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, asdict
from datetime import datetime, UTC
from pathlib import Path
from typing import Any


try:
    from bleak import BleakClient, BleakScanner
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: bleak. Install with: pip install bleak"
    ) from exc


@dataclass
class ScannedDevice:
    name: str | None
    address: str
    rssi: int | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan and inspect BrainLink BLE devices."
    )
    parser.add_argument(
        "--scan-time",
        type=float,
        default=12.0,
        help="BLE scan duration in seconds (default: 12).",
    )
    parser.add_argument(
        "--name-filter",
        default="BrainLink",
        help="Case-insensitive name filter for candidate devices (default: BrainLink).",
    )
    parser.add_argument(
        "--address",
        default="",
        help="Optional explicit BLE address to inspect with GATT discovery.",
    )
    parser.add_argument(
        "--output",
        default="docs/specs/brainlink-ble-probe.json",
        help="Path to JSON output file.",
    )
    return parser.parse_args()


async def scan_devices(scan_time: float) -> list[ScannedDevice]:
    result: list[ScannedDevice] = []

    # Newer bleak versions can return advertisement metadata (including RSSI).
    try:
        devices_with_adv = await BleakScanner.discover(
            timeout=scan_time,
            return_adv=True,
        )
        for d, adv in devices_with_adv.values():
            result.append(
                ScannedDevice(
                    name=d.name,
                    address=d.address,
                    rssi=getattr(adv, "rssi", None),
                )
            )
        return result
    except TypeError:
        # Older bleak API: no return_adv support.
        pass

    devices = await BleakScanner.discover(timeout=scan_time)
    for d in devices:
        result.append(
            ScannedDevice(
                name=d.name,
                address=d.address,
                rssi=getattr(d, "rssi", None),
            )
        )
    return result


def filter_candidates(devices: list[ScannedDevice], name_filter: str) -> list[ScannedDevice]:
    needle = name_filter.lower().strip()
    if not needle:
        return devices
    filtered: list[ScannedDevice] = []
    for d in devices:
        if d.name and needle in d.name.lower():
            filtered.append(d)
    return filtered


async def inspect_gatt(address: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "address": address,
        "connected": False,
        "services": [],
        "error": None,
    }
    try:
        async with BleakClient(address) as client:
            report["connected"] = bool(client.is_connected)
            services_collection: Any
            get_services = getattr(client, "get_services", None)
            if callable(get_services):
                services_collection = await get_services()
            else:
                # Some bleak versions expose services via property only.
                services_collection = getattr(client, "services", None)

            if services_collection is None:
                raise RuntimeError("Unable to read GATT services from BleakClient")

            services_iterable = (
                services_collection.services.values()
                if hasattr(services_collection, "services")
                else services_collection
            )

            for service in services_iterable:
                service_payload: dict[str, Any] = {
                    "uuid": service.uuid,
                    "description": service.description,
                    "characteristics": [],
                }
                for characteristic in service.characteristics:
                    service_payload["characteristics"].append(
                        {
                            "uuid": characteristic.uuid,
                            "description": characteristic.description,
                            "properties": list(characteristic.properties),
                            "handle": characteristic.handle,
                        }
                    )
                report["services"].append(service_payload)
    except Exception as err:  # pragma: no cover
        report["error"] = str(err)
    return report


async def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).isoformat()
    scanned = await scan_devices(args.scan_time)
    candidates = filter_candidates(scanned, args.name_filter)

    inspect_address = args.address.strip()
    if not inspect_address and candidates:
        inspect_address = candidates[0].address

    gatt_report: dict[str, Any] | None = None
    if inspect_address:
        gatt_report = await inspect_gatt(inspect_address)

    payload = {
        "timestamp_utc": timestamp,
        "scan_time_sec": args.scan_time,
        "name_filter": args.name_filter,
        "devices_total": len(scanned),
        "devices": [asdict(x) for x in scanned],
        "candidates_total": len(candidates),
        "candidates": [asdict(x) for x in candidates],
        "gatt_probe": gatt_report,
    }

    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Saved report to: {output_path}")
    print(f"Found devices: {len(scanned)} | candidates: {len(candidates)}")
    if gatt_report:
        print(f"GATT probe target: {inspect_address}")
        if gatt_report.get("error"):
            print(f"GATT probe error: {gatt_report['error']}")
    else:
        print("No probe target selected (no candidates and no --address).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
