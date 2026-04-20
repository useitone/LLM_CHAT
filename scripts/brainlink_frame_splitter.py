#!/usr/bin/env python3
"""
Split BrainLink raw notify payloads into framed packets.

Frame heuristic:
- start marker: aaaa
- end marker:   2323
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

START = bytes.fromhex("aaaa")
END = bytes.fromhex("2323")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split BrainLink raw JSONL capture into frame statistics."
    )
    parser.add_argument(
        "--input",
        default="docs/specs/brainlink-raw-capture.jsonl",
        help="Input JSONL from brainlink_stream_capture.py",
    )
    parser.add_argument(
        "--output",
        default="docs/specs/brainlink-frame-analysis.json",
        help="Output JSON analysis path.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="How many top signatures to keep (default: 20).",
    )
    return parser.parse_args()


def split_frames(payload: bytes) -> list[bytes]:
    frames: list[bytes] = []
    i = 0
    n = len(payload)
    while i < n:
        start = payload.find(START, i)
        if start == -1:
            break
        end = payload.find(END, start + len(START))
        if end == -1:
            break
        frame = payload[start : end + len(END)]
        frames.append(frame)
        i = end + len(END)
    return frames


def analyze(input_path: Path, top_n: int) -> dict[str, Any]:
    line_count = 0
    payload_bytes_total = 0
    raw_len_counter: Counter[int] = Counter()
    frame_len_counter: Counter[int] = Counter()
    frame_prefix_counter: Counter[str] = Counter()
    frame_counter = 0
    unparsable_lines = 0
    records_without_hex = 0
    records_without_frames = 0

    sample_frames: list[str] = []

    with input_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            line_count += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                unparsable_lines += 1
                continue

            hex_payload = rec.get("hex", "")
            if not hex_payload:
                records_without_hex += 1
                continue

            try:
                payload = bytes.fromhex(hex_payload)
            except ValueError:
                unparsable_lines += 1
                continue

            payload_bytes_total += len(payload)
            raw_len_counter[len(payload)] += 1

            frames = split_frames(payload)
            if not frames:
                records_without_frames += 1
                continue

            for fr in frames:
                frame_counter += 1
                frame_len_counter[len(fr)] += 1
                frame_prefix_counter[fr[:8].hex()] += 1
                if len(sample_frames) < 10:
                    sample_frames.append(fr.hex())

    return {
        "input_file": str(input_path),
        "records_total": line_count,
        "records_unparsable": unparsable_lines,
        "records_without_hex": records_without_hex,
        "records_without_frames": records_without_frames,
        "payload_bytes_total": payload_bytes_total,
        "raw_payload_lengths_top": [
            {"len": k, "count": v} for k, v in raw_len_counter.most_common(top_n)
        ],
        "frames_total": frame_counter,
        "frame_lengths_top": [
            {"len": k, "count": v} for k, v in frame_len_counter.most_common(top_n)
        ],
        "frame_prefixes_top": [
            {"prefix_hex": k, "count": v}
            for k, v in frame_prefix_counter.most_common(top_n)
        ],
        "sample_frames_hex": sample_frames,
    }


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = analyze(input_path, args.top)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Analysis saved to: {output_path}")
    print(f"Records: {report['records_total']} | Frames: {report['frames_total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
