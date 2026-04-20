#!/usr/bin/env python3
"""
Одна сессия COM (Macrotellect) + BLE: захват, сверка, краткий вывод align_timestamp.

Всегда переключает cwd на корень репозитория (эквивалент «cd в корень»).

Примеры:
  python scripts/brainlink_verify_one_session.py --capture --address C0:E2:FC:2D:AC:10
  python scripts/brainlink_verify_one_session.py --compare --stem docs/specs/brainlink-concurrent-session
  python scripts/brainlink_verify_one_session.py --summary --report docs/specs/brainlink-macrotellect-vs-ble.json
  python scripts/brainlink_verify_one_session.py --all --address C0:E2:FC:2D:AC:10 --stem docs/specs/brainlink-concurrent-session
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _chdir_root() -> None:
    os.chdir(ROOT)


def _run(cmd: list[str]) -> int:
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))


def cmd_capture(args: argparse.Namespace) -> int:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "brainlink_concurrent_capture.py"),
        "--address",
        args.address,
        "--port",
        args.port,
        "--baud",
        str(args.baud),
        "--duration",
        str(args.duration),
        "--stem",
        str(args.stem),
    ]
    if args.init_hex:
        cmd.extend(["--init-hex", args.init_hex])
    return _run(cmd)


def cmd_compare(args: argparse.Namespace) -> int:
    stem = Path(args.stem)
    mac = Path(str(stem) + "-macrotellect.jsonl")
    ble = Path(str(stem) + "-ble.jsonl")
    out = Path(args.report)
    if not mac.is_file():
        print(f"Missing file: {mac}", file=sys.stderr)
        return 1
    if not ble.is_file():
        print(f"Missing file: {ble}", file=sys.stderr)
        return 1
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "brainlink_compare_macrotellect_ble.py"),
        "--macrotellect",
        str(mac),
        "--ble-raw",
        str(ble),
        "--output",
        str(out),
        "--align",
        "all",
        "--time-tol",
        str(args.time_tol),
    ]
    return _run(cmd)


def print_summary(report_path: Path) -> int:
    if not report_path.is_file():
        print(f"Missing report: {report_path}", file=sys.stderr)
        return 1
    data: dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
    print("\n=== Report summary (trust align_timestamp for one session) ===", flush=True)
    print(f"macrotellect_eeg_events: {data.get('macrotellect_eeg_events')}", flush=True)
    print(f"ble_eeg_frames_decoded: {data.get('ble_eeg_frames_decoded')}", flush=True)
    at = data.get("align_timestamp")
    if not isinstance(at, dict) or not at:
        print(
            "align_timestamp: (missing — re-run brainlink_compare_macrotellect_ble.py "
            "from repo root, or use --compare in this script)",
            flush=True,
        )
    else:
        mp = at.get("matched_pairs")
        print(f"align_timestamp.matched_pairs: {mp}", flush=True)
        if mp == 0:
            print(
                "  hint: 0 pairs usually means COM and BLE files are not overlapping in time "
                "(run brainlink_concurrent_capture.py once).",
                flush=True,
            )
        print(f"align_timestamp.median_abs_delta_t_ms: {at.get('median_abs_delta_t_ms')}", flush=True)
        mae = at.get("mae_over_matched") or {}
        if mae:
            worst = max(mae.items(), key=lambda kv: kv[1])
            print(f"align_timestamp.mae_over_matched (worst field): {worst[0]} = {worst[1]}", flush=True)
            for k, v in sorted(mae.items(), key=lambda kv: -kv[1])[:5]:
                print(f"  {k}: {v}", flush=True)
        fp = at.get("first_pairs") or []
        if fp:
            p0 = fp[0]
            print("\nFirst timestamp-aligned pair:", flush=True)
            print(
                f"  mac_index={p0.get('mac_index')} ble_index={p0.get('ble_index')} "
                f"abs_delta_t_sec={p0.get('abs_delta_t_sec')}",
                flush=True,
            )
    idx = data.get("align_index") or {}
    print("\n(secondary) align_index.aligned_count:", idx.get("aligned_count"), flush=True)
    rel = data.get("align_relative") or {}
    print("(secondary) align_relative.matched_pairs:", rel.get("matched_pairs"), flush=True)
    print(f"\nFull report: {report_path.resolve()}", flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Сверка COM+Macrotellect и BLE за одну сессию (оркестратор)."
    )
    p.add_argument(
        "--stem",
        type=Path,
        default=Path("docs/specs/brainlink-concurrent-session"),
        help="Префикс файлов как у brainlink_concurrent_capture.py",
    )
    p.add_argument(
        "--report",
        type=Path,
        default=Path("docs/specs/brainlink-macrotellect-vs-ble.json"),
        help="JSON отчёт compare",
    )
    p.add_argument("--time-tol", type=float, default=2.0)
    p.add_argument("--address", default="", help="BLE MAC (для --capture / --all)")
    p.add_argument("--port", default="COM3")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--duration", type=float, default=45.0)
    p.add_argument("--init-hex", default="")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--capture",
        action="store_true",
        help="Только совместный захват (нужен --address)",
    )
    g.add_argument("--compare", action="store_true", help="Только compare по --stem")
    g.add_argument(
        "--summary",
        action="store_true",
        help="Только вывод сводки по --report",
    )
    g.add_argument(
        "--all",
        action="store_true",
        help="capture + compare + summary (нужен --address)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    _chdir_root()
    print(f"Repo root: {ROOT}", flush=True)

    if args.capture or args.all:
        if not args.address:
            print("BLE --address is required for capture / --all.", file=sys.stderr)
            return 1

    if args.capture:
        return cmd_capture(args)

    if args.compare:
        code = cmd_compare(args)
        if code != 0:
            return code
        return print_summary(args.report)

    if args.summary:
        return print_summary(args.report)

    if args.all:
        code = cmd_capture(args)
        if code != 0:
            return code
        code = cmd_compare(args)
        if code != 0:
            return code
        return print_summary(args.report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
