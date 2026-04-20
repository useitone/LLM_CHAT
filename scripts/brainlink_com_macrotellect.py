#!/usr/bin/env python3
"""
Stream BrainLink Pro over virtual COM using Macrotellect official BrainLinkParser.pyd.

Requires a local copy of BrainLinkParser.pyd (Windows). See:
  docs/specs/vendor/macrotellect_brainlink_parser/README.md
https://github.com/Macrotellect/BrainLinkParser-Python

Uses pyserial (same as brainlink_com_capture.py). Parsed events are written as JSONL.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Callable, TextIO

try:
    import serial
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: pyserial. Install: pip install -e ."
    ) from exc

DEFAULT_PYD_DIR = Path(__file__).resolve().parent.parent / "docs/specs/vendor/macrotellect_brainlink_parser"


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
            "Match Python version/bitness to the .pyd (upstream documents Python 3.11)."
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
        description="COM stream → Macrotellect BrainLinkParser → JSONL."
    )
    p.add_argument(
        "--pyd-dir",
        type=Path,
        default=DEFAULT_PYD_DIR,
        help="Directory containing BrainLinkParser.pyd",
    )
    p.add_argument("--port", default="COM3", help="Serial port (default: COM3).")
    p.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200).")
    p.add_argument(
        "--duration",
        type=float,
        default=30.0,
        help="Seconds to run (default: 30). Use 0 for until Ctrl+C.",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("docs/specs/brainlink-com-macrotellect.jsonl"),
        help="JSONL output path.",
    )
    p.add_argument("--read-size", type=int, default=4096, help="Max bytes per read().")
    p.add_argument(
        "--print-eeg",
        action="store_true",
        help="Also print EEG lines to stderr for quick visual check.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    BrainLinkParser = load_brain_link_parser_class(args.pyd_dir)

    out_path: Path = args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)

    out_file: TextIO
    out_file = out_path.open("w", encoding="utf-8")

    def write_event(kind: str, payload: dict[str, Any]) -> None:
        rec = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "type": kind,
            **payload,
        }
        out_file.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out_file.flush()

    def on_eeg(data: Any) -> None:
        d = _eeg_dict(data)
        write_event("eeg", {"eeg": d})
        if args.print_eeg:
            print(
                f"ATT {d.get('attention', 0):3d} | MED {d.get('meditation', 0):3d} | "
                f"sig {d.get('signal', 0):3d}",
                file=sys.stderr,
            )

    def on_extend(data: Any) -> None:
        write_event("extend", {"extend": _extend_dict(data)})

    def on_gyro(x: int, y: int, z: int) -> None:
        write_event("gyro", {"x": x, "y": y, "z": z})

    def on_rr(rr1: int, rr2: int, rr3: int) -> None:
        write_event("rr", {"rr1": rr1, "rr2": rr2, "rr3": rr3})

    def on_raw(raw: int) -> None:
        write_event("raw", {"raw": int(raw)})

    parser = BrainLinkParser(on_eeg, on_extend, on_gyro, on_rr, on_raw)

    print(f"Opening {args.port} @ {args.baud} …", file=sys.stderr)
    print(f"BrainLinkParser from: {args.pyd_dir.resolve()}", file=sys.stderr)
    print(f"Logging to: {out_path}", file=sys.stderr)

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
        print(f"Serial error: {e}", file=sys.stderr)
        out_file.close()
        return 1

    end_time = None if args.duration <= 0 else time.monotonic() + args.duration
    chunks = 0

    try:
        while True:
            if end_time is not None and time.monotonic() >= end_time:
                break
            chunk = ser.read(args.read_size)
            if chunk:
                chunks += 1
                parser.parse(bytes(chunk))
    except KeyboardInterrupt:
        print("\nStopped (Ctrl+C).", file=sys.stderr)
    finally:
        ser.close()
        out_file.close()

    print(f"Serial read chunks: {chunks}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
