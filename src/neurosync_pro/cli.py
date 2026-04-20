"""Command-line entry: decode, compare, concurrent capture, optional UI."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def cmd_decode(args: argparse.Namespace) -> int:
    from neurosync_pro.eeg.protocol import run

    inp = Path(args.input)
    out = Path(args.output)
    if not inp.exists():
        print(f"Input not found: {inp}", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    report = run(inp, args.max_samples)
    report["input_file"] = str(inp)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report saved to: {out}")
    print(
        "Counts: EEG={eeg_count} short={short_count} gyro={gyro_count} "
        "extend={extend_count} skipped_aa={skipped_at_aa}".format(**report)
    )
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    script = _repo_root() / "scripts" / "brainlink_compare_macrotellect_ble.py"
    cmd = [
        sys.executable,
        str(script),
        "--macrotellect",
        str(args.macrotellect),
        "--ble-raw",
        str(args.ble_raw),
        "--output",
        str(args.output),
        "--pairs",
        str(args.pairs),
        "--align",
        args.align,
        "--time-tol",
        str(args.time_tol),
    ]
    return subprocess.call(cmd)


def cmd_concurrent_capture(args: argparse.Namespace) -> int:
    script = _repo_root() / "scripts" / "brainlink_concurrent_capture.py"
    cmd = [
        sys.executable,
        str(script),
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
    return subprocess.call(cmd)


def cmd_eeg_replay(args: argparse.Namespace) -> int:
    try:
        from neurosync_pro.ui.replay_plot import run_replay_plot
    except ImportError as e:
        print(
            "Install GUI extras: pip install -e \".[gui]\"",
            file=sys.stderr,
        )
        print(e, file=sys.stderr)
        return 1
    return run_replay_plot(
        Path(args.input),
        float(args.interval_ms),
    )


def cmd_agent_serve(args: argparse.Namespace) -> int:
    from neurosync_pro.agent.server import start_agent_api, stop_agent_api
    from neurosync_pro.bus import EventBus

    bus = EventBus()
    server, _thr = start_agent_api(bus, host=args.host, port=args.port)
    print(
        f"POST http://{args.host}:{args.port}/v1/event",
        'body: {"topic":"eeg.tick","payload":{"x":1}}',
        flush=True,
    )
    try:
        import time

        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        stop_agent_api(server)
        print("Stopped.", flush=True)
    return 0


def cmd_meditation(args: argparse.Namespace) -> int:
    try:
        from neurosync_pro.eeg.ble_stream import normalize_ble_address
        from neurosync_pro.ui.meditation_poc import run_meditation_poc
    except ImportError as e:
        print("Install GUI extras: pip install -e \".[gui]\"", file=sys.stderr)
        print(e, file=sys.stderr)
        return 1
    raw = (args.ble_address or "").strip()
    ble_addr = normalize_ble_address(raw) if raw else ""
    return run_meditation_poc(
        jsonl_path=Path(args.input) if args.input else None,
        ble_address=ble_addr or None,
        ble_init_hex=(args.ble_init_hex or "").strip(),
        ble_duration_s=float(args.ble_duration) if args.ble_duration is not None else None,
        session_log_path=Path(args.session_log) if args.session_log else None,
        auto_start_ble=bool(ble_addr),
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="neurosync-pro",
        description="NeuroSync Pro — EEG decode, compare, capture, GUI (optional).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("decode", help="Decode BrainLink BLE JSONL → frame report JSON.")
    d.add_argument(
        "--input",
        type=Path,
        default=Path("docs/specs/brainlink-raw-capture.jsonl"),
    )
    d.add_argument(
        "--output",
        type=Path,
        default=Path("docs/specs/brainlink-frame-decode-report.json"),
    )
    d.add_argument("--max-samples", type=int, default=5)
    d.set_defaults(func=cmd_decode)

    c = sub.add_parser(
        "compare",
        help="Macrotellect COM JSONL vs BLE JSONL (runs scripts/brainlink_compare_macrotellect_ble.py).",
    )
    c.add_argument(
        "--macrotellect",
        type=Path,
        default=Path("docs/specs/brainlink-com-macrotellect.jsonl"),
    )
    c.add_argument(
        "--ble-raw",
        type=Path,
        default=Path("docs/specs/brainlink-raw-capture.jsonl"),
    )
    c.add_argument(
        "--output",
        type=Path,
        default=Path("docs/specs/brainlink-macrotellect-vs-ble.json"),
    )
    c.add_argument("--pairs", type=int, default=10)
    c.add_argument(
        "--align",
        choices=("index", "timestamp", "relative", "all"),
        default="all",
    )
    c.add_argument("--time-tol", type=float, default=2.0)
    c.set_defaults(func=cmd_compare)

    cc = sub.add_parser(
        "concurrent-capture",
        help="COM Macrotellect + BLE capture (scripts/brainlink_concurrent_capture.py).",
    )
    cc.add_argument("--address", required=True)
    cc.add_argument("--port", default="COM3")
    cc.add_argument("--baud", type=int, default=115200)
    cc.add_argument("--duration", type=float, default=30.0)
    cc.add_argument(
        "--stem",
        type=Path,
        default=Path("docs/specs/brainlink-concurrent-session"),
    )
    cc.add_argument("--init-hex", default="")
    cc.set_defaults(func=cmd_concurrent_capture)

    er = sub.add_parser(
        "eeg-replay",
        help="Minimal PySide6 plot from Macrotellect-style JSONL (eeg lines).",
    )
    er.add_argument(
        "--input",
        type=Path,
        default=Path("docs/specs/brainlink-com-macrotellect.jsonl"),
    )
    er.add_argument("--interval-ms", type=float, default=100.0)
    er.set_defaults(func=cmd_eeg_replay)

    med = sub.add_parser(
        "meditation",
        help="PoC: meditation/concentration hints + EEG (JSONL replay or live BLE).",
    )
    med.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Optional JSONL with type=eeg for replay (ignored if --ble-address is set).",
    )
    med.add_argument(
        "--ble-address",
        type=str,
        default="",
        help="BrainLink BLE MAC, e.g. C0:E2:FC:2D:AC:10 (live metrics; auto-starts session).",
    )
    med.add_argument(
        "--ble-init-hex",
        type=str,
        default="",
        help="Optional init command hex sent to Nordic UART write characteristic.",
    )
    med.add_argument(
        "--ble-duration",
        type=float,
        default=None,
        help="Stop BLE session after this many seconds (optional; else run until window close).",
    )
    med.add_argument(
        "--session-log",
        type=Path,
        default=None,
        help="Append JSONL lines type=eeg (attention/meditation) during session.",
    )
    med.set_defaults(func=cmd_meditation)

    ag = sub.add_parser(
        "agent-serve",
        help="Headless EventBus + POST /v1/event on localhost (Ctrl+C to stop).",
    )
    ag.add_argument("--host", default="127.0.0.1")
    ag.add_argument("--port", type=int, default=8765)
    ag.set_defaults(func=cmd_agent_serve)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
