#!/usr/bin/env python3
"""
Capture raw bytes from BrainLink virtual COM port (Windows).

No third-party protocol parser — only timestamps and hex payloads for
comparison with BLE captures (e.g. brainlink_stream_capture.py).

Typical BrainLink Pro setup (Macrotellect docs): 115200 8N1, COM output port.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

try:
    import serial
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: pyserial. Install with: pip install pyserial\n"
        "Or from repo root: pip install -e ."
    ) from exc


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Capture raw serial stream from BrainLink COM port to JSONL."
    )
    p.add_argument(
        "--port",
        default="COM3",
        help="Serial port name (default: COM3).",
    )
    p.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="Baud rate (default: 115200).",
    )
    p.add_argument(
        "--duration",
        type=float,
        default=30.0,
        help="Capture duration in seconds (default: 30). Use 0 for until Ctrl+C.",
    )
    p.add_argument(
        "--output",
        default="docs/specs/brainlink-com-raw-capture.jsonl",
        help="Output JSONL path.",
    )
    p.add_argument(
        "--read-size",
        type=int,
        default=4096,
        help="Max bytes per read() call (default: 4096).",
    )
    return p.parse_args()


def record_line(out, chunk: bytes) -> None:
    rec: dict[str, Any] = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "len": len(chunk),
        "hex": chunk.hex(),
        "source": "serial",
    }
    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    out.flush()


def main() -> int:
    args = parse_args()
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Opening {args.port} @ {args.baud} 8N1 …")
    try:
        ser = serial.Serial(
            port=args.port,
            baudrate=args.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.25,
        )
    except serial.SerialException as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    packet_count = 0
    end_time = None if args.duration <= 0 else time.monotonic() + args.duration

    print(
        f"Writing to {out_path} — "
        f"{'until Ctrl+C' if args.duration <= 0 else f'{args.duration:.0f} s'}"
    )

    try:
        with out_path.open("w", encoding="utf-8") as out:
            while True:
                if end_time is not None and time.monotonic() >= end_time:
                    break
                chunk = ser.read(args.read_size)
                if chunk:
                    packet_count += 1
                    record_line(out, chunk)
    except KeyboardInterrupt:
        print("\nStopped by user (Ctrl+C).")
    finally:
        ser.close()

    print(f"Done. Chunks written: {packet_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
