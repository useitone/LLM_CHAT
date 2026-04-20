#!/usr/bin/env python3
"""
Decode BrainLink UART frames from raw JSONL captures.

Implementation: neurosync_pro.eeg.protocol (this file is the CLI wrapper).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from neurosync_pro.eeg.protocol import run  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Decode BrainLink frames from JSONL capture.")
    p.add_argument(
        "--input",
        default="docs/specs/brainlink-raw-capture.jsonl",
        help="Input JSONL from brainlink_stream_capture.py",
    )
    p.add_argument(
        "--output",
        default="docs/specs/brainlink-frame-decode-report.json",
        help="Output JSON report path.",
    )
    p.add_argument(
        "--max-samples",
        type=int,
        default=5,
        help="Max sample records per type in report (default: 5).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    inp = Path(args.input)
    out = Path(args.output)
    if not inp.exists():
        raise SystemExit(f"Input not found: {inp}")

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


if __name__ == "__main__":
    raise SystemExit(main())
