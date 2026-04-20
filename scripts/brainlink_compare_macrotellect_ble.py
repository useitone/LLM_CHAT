#!/usr/bin/env python3
"""
Compare Macrotellect COM JSONL (type=eeg) with BLE raw JSONL decoded by neurosync_pro.eeg.protocol.

Alignment modes:
  index       — i-th Macrotellect EEG vs i-th BLE-decoded EEG (legacy).
  timestamp   — nearest wall-clock time (needs one concurrent capture; same UTC).
  relative    — seconds since first EEG in each file; tolerates clock offset / drops.
  all         — emit index, timestamp, and relative blocks (default).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def _ensure_src_on_path() -> None:
    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _extract_all_eeg_frames():
    _ensure_src_on_path()
    from neurosync_pro.eeg.protocol import extract_all_eeg_frames

    return extract_all_eeg_frames


def _parse_ts(s: Any) -> datetime | None:
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_macrotellect_eeg(path: Path) -> list[dict[str, Any]]:
    """Each row: timestamp_utc (optional), eeg fields in BLE-shaped keys."""
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("type") != "eeg":
                continue
            eeg = o.get("eeg")
            if not isinstance(eeg, dict):
                continue
            row = mac_to_ble_shape(eeg)
            ts = o.get("timestamp_utc")
            if isinstance(ts, str):
                row["_timestamp_utc"] = ts
            rows.append(row)
    return rows


def mac_to_ble_shape(m: dict[str, Any]) -> dict[str, Any]:
    """Macrotellect camelCase → same keys as BLE decoder asdict."""
    return {
        "attention": int(m.get("attention", 0)),
        "meditation": int(m.get("meditation", 0)),
        "delta": int(m.get("delta", 0)),
        "theta": int(m.get("theta", 0)),
        "low_alpha": int(m.get("lowAlpha", 0)),
        "high_alpha": int(m.get("highAlpha", 0)),
        "low_beta": int(m.get("lowBeta", 0)),
        "high_beta": int(m.get("highBeta", 0)),
        "low_gamma": int(m.get("lowGamma", 0)),
        "high_gamma": int(m.get("highGamma", 0)),
    }


def _values_only(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in row if not k.startswith("_")}


def _mae_for_pairs(
    mac_vals: list[dict[str, int]],
    ble_vals: list[dict[str, int]],
    keys: list[str],
) -> dict[str, float]:
    n = min(len(mac_vals), len(ble_vals))
    if n <= 0 or not keys:
        return {}
    out: dict[str, float] = {}
    for k in keys:
        errs = [abs(mac_vals[i][k] - ble_vals[i][k]) for i in range(n)]
        out[k] = round(statistics.fmean(errs), 4)
    return out


def _build_pairs(
    mac_vals: list[dict[str, int]],
    ble_vals: list[dict[str, int]],
    keys: list[str],
    limit: int,
    label_mac: str = "macrotellect",
    label_ble: str = "ble_decoder",
) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    n = min(len(mac_vals), len(ble_vals), limit)
    for i in range(n):
        pairs.append(
            {
                "index": i,
                label_mac: mac_vals[i],
                label_ble: ble_vals[i],
                "abs_diff": {k: abs(mac_vals[i][k] - ble_vals[i][k]) for k in keys},
            }
        )
    return pairs


def align_timestamp_nearest(
    mac_rows: list[dict[str, Any]],
    ble_rows: list[dict[str, Any]],
    tol_sec: float,
) -> list[tuple[int, int, float]]:
    """
    Greedy: walk Macrotellect EEGs in time order; each picks closest unused BLE
    sample within tol_sec (by source_timestamp_utc).
    Returns (mac_index, ble_index, delta_sec).
    """
    mac_ts_idx: list[tuple[datetime, int]] = []
    for i, r in enumerate(mac_rows):
        ts = _parse_ts(r.get("_timestamp_utc"))
        if ts is not None:
            mac_ts_idx.append((ts, i))
    mac_ts_idx.sort(key=lambda x: x[0])

    ble_ts_idx: list[tuple[datetime, int]] = []
    for j, r in enumerate(ble_rows):
        ts = _parse_ts(r.get("source_timestamp_utc"))
        if ts is not None:
            ble_ts_idx.append((ts, j))
    ble_ts_idx.sort(key=lambda x: x[0])

    if not mac_ts_idx or not ble_ts_idx:
        return []

    used_ble: set[int] = set()
    pairs: list[tuple[int, int, float]] = []

    for m_ts, mi in mac_ts_idx:
        best_j: int | None = None
        best_dt = tol_sec + 1.0
        for b_ts, bj in ble_ts_idx:
            if bj in used_ble:
                continue
            dt = abs((m_ts - b_ts).total_seconds())
            if dt <= tol_sec and dt < best_dt:
                best_dt = dt
                best_j = bj
        if best_j is not None:
            pairs.append((mi, best_j, best_dt))
            used_ble.add(best_j)

    return pairs


def align_relative_from_first_eeg(
    mac_rows: list[dict[str, Any]],
    ble_rows: list[dict[str, Any]],
    tol_sec: float,
) -> list[tuple[int, int, float]]:
    """
    Same order as in files. rel = t - t(first EEG). Greedy: each Mac row picks
    closest unused BLE by |rel_mac - rel_ble| within tol_sec.
    """
    mac_parsed: list[tuple[float, int]] = []
    t0m: datetime | None = None
    for i, r in enumerate(mac_rows):
        ts = _parse_ts(r.get("_timestamp_utc"))
        if ts is None:
            return []
        if t0m is None:
            t0m = ts
        mac_parsed.append(((ts - t0m).total_seconds(), i))

    ble_parsed: list[tuple[float, int]] = []
    t0b: datetime | None = None
    for j, r in enumerate(ble_rows):
        ts = _parse_ts(r.get("source_timestamp_utc"))
        if ts is None:
            return []
        if t0b is None:
            t0b = ts
        ble_parsed.append(((ts - t0b).total_seconds(), j))

    used_ble: set[int] = set()
    pairs: list[tuple[int, int, float]] = []
    for rel_m, mi in mac_parsed:
        best_j: int | None = None
        best_d = tol_sec + 1.0
        for rel_b, bj in ble_parsed:
            if bj in used_ble:
                continue
            d = abs(rel_m - rel_b)
            if d <= tol_sec and d < best_d:
                best_d = d
                best_j = bj
        if best_j is not None:
            pairs.append((mi, best_j, best_d))
            used_ble.add(best_j)
    return pairs


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare Macrotellect EEG JSONL vs BLE decode.")
    p.add_argument(
        "--macrotellect",
        type=Path,
        default=Path("docs/specs/brainlink-com-macrotellect.jsonl"),
        help="JSONL from brainlink_com_macrotellect.py",
    )
    p.add_argument(
        "--ble-raw",
        type=Path,
        default=Path("docs/specs/brainlink-raw-capture.jsonl"),
        help="JSONL from brainlink_stream_capture.py",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("docs/specs/brainlink-macrotellect-vs-ble.json"),
        help="JSON report output.",
    )
    p.add_argument(
        "--pairs",
        type=int,
        default=10,
        help="How many first pairs to include per alignment block.",
    )
    p.add_argument(
        "--align",
        choices=("index", "timestamp", "relative", "all"),
        default="all",
        help="How to pair rows (default: all).",
    )
    p.add_argument(
        "--time-tol",
        type=float,
        default=2.0,
        help="Max |Δt| in seconds for timestamp alignment (default: 2).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.macrotellect.exists():
        raise SystemExit(f"Not found: {args.macrotellect}")
    if not args.ble_raw.exists():
        raise SystemExit(f"Not found: {args.ble_raw}")

    extract = _extract_all_eeg_frames()

    mac = load_macrotellect_eeg(args.macrotellect)
    ble_raw = extract(args.ble_raw)

    keys = [
        "attention",
        "meditation",
        "delta",
        "theta",
        "low_alpha",
        "high_alpha",
        "low_beta",
        "high_beta",
        "low_gamma",
        "high_gamma",
    ]

    ble: list[dict[str, Any]] = []
    for row in ble_raw:
        d: dict[str, Any] = {
            "attention": int(row["attention"]),
            "meditation": int(row["meditation"]),
            "delta": int(row["delta"]),
            "theta": int(row["theta"]),
            "low_alpha": int(row["low_alpha"]),
            "high_alpha": int(row["high_alpha"]),
            "low_beta": int(row["low_beta"]),
            "high_beta": int(row["high_beta"]),
            "low_gamma": int(row["low_gamma"]),
            "high_gamma": int(row["high_gamma"]),
        }
        st = row.get("source_timestamp_utc")
        if isinstance(st, str):
            d["source_timestamp_utc"] = st
        ble.append(d)

    report: dict[str, Any] = {
        "macrotellect_eeg_events": len(mac),
        "ble_eeg_frames_decoded": len(ble),
        "ble_rows_with_timestamp": sum(
            1 for r in ble if isinstance(r.get("source_timestamp_utc"), str)
        ),
        "mac_rows_with_timestamp": sum(
            1 for r in mac if isinstance(r.get("_timestamp_utc"), str)
        ),
    }

    want_index = args.align in ("index", "all")
    want_ts = args.align in ("timestamp", "all")
    want_rel = args.align in ("relative", "all")

    if want_index:
        n = min(len(mac), len(ble))
        mac_v = [_values_only(mac[i]) for i in range(n)]
        ble_v = [_values_only(ble[i]) for i in range(n)]
        mae = _mae_for_pairs(mac_v, ble_v, keys)
        report["align_index"] = {
            "aligned_count": n,
            "note": "Pairs aligned by stream order (0..n-1). Misleading if captures differ.",
            "mae_over_aligned": mae,
            "first_pairs": _build_pairs(mac_v, ble_v, keys, args.pairs),
        }

    if want_ts:
        idx_pairs = align_timestamp_nearest(mac, ble, args.time_tol)
        mac_v = [_values_only(mac[mi]) for mi, _bj, _dt in idx_pairs]
        ble_v = [_values_only(ble[bj]) for _mi, bj, _dt in idx_pairs]
        mae_ts = _mae_for_pairs(mac_v, ble_v, keys)
        dts_ms = [dt * 1000.0 for _mi, _bj, dt in idx_pairs]
        median_ms = round(statistics.median(dts_ms), 2) if dts_ms else None

        ts_first: list[dict[str, Any]] = []
        for k in range(min(len(idx_pairs), args.pairs)):
            mi, bj, dt = idx_pairs[k]
            mv = _values_only(mac[mi])
            bv = _values_only(ble[bj])
            ts_first.append(
                {
                    "mac_index": mi,
                    "ble_index": bj,
                    "abs_delta_t_sec": round(dt, 6),
                    "macrotellect": mv,
                    "ble_decoder": bv,
                    "abs_diff": {kk: abs(mv[kk] - bv[kk]) for kk in keys},
                }
            )

        report["align_timestamp"] = {
            "time_tol_sec": args.time_tol,
            "matched_pairs": len(idx_pairs),
            "median_abs_delta_t_ms": median_ms,
            "note": (
                "Greedy nearest-neighbor in wall-clock order; each BLE row used at most once. "
                "Requires COM and BLE JSONL from the same physical run (overlapping UTC)."
            ),
            "mae_over_matched": mae_ts,
            "first_pairs": ts_first,
        }

    if want_rel:
        rel_pairs = align_relative_from_first_eeg(mac, ble, args.time_tol)
        mac_v = [_values_only(mac[mi]) for mi, _bj, _dt in rel_pairs]
        ble_v = [_values_only(ble[bj]) for _mi, bj, _dt in rel_pairs]
        mae_r = _mae_for_pairs(mac_v, ble_v, keys)
        rel_first: list[dict[str, Any]] = []
        for k in range(min(len(rel_pairs), args.pairs)):
            mi, bj, dt = rel_pairs[k]
            mv = _values_only(mac[mi])
            bv = _values_only(ble[bj])
            rel_first.append(
                {
                    "mac_index": mi,
                    "ble_index": bj,
                    "abs_delta_rel_sec": round(dt, 6),
                    "macrotellect": mv,
                    "ble_decoder": bv,
                    "abs_diff": {kk: abs(mv[kk] - bv[kk]) for kk in keys},
                }
            )
        report["align_relative"] = {
            "time_tol_sec": args.time_tol,
            "matched_pairs": len(rel_pairs),
            "note": (
                "Delta-t from first EEG in each file. Can pair unrelated captures if their "
                "sample spacing is similar; trust MAE, not the pair count alone."
            ),
            "mae_over_matched": mae_r,
            "first_pairs": rel_first,
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Report: {args.output}")
    print(
        f"Macrotellect EEG: {len(mac)} | BLE EEG: {len(ble)} "
        f"| mac ts: {report['mac_rows_with_timestamp']} | ble ts: {report['ble_rows_with_timestamp']}"
    )
    if want_index and report.get("align_index", {}).get("mae_over_aligned"):
        mae = report["align_index"]["mae_over_aligned"]
        worst = max(mae.items(), key=lambda kv: kv[1])
        print(f"[index] aligned={report['align_index']['aligned_count']} largest MAE: {worst[0]} = {worst[1]}")
    if want_ts and report.get("align_timestamp", {}).get("mae_over_matched"):
        mae = report["align_timestamp"]["mae_over_matched"]
        worst = max(mae.items(), key=lambda kv: kv[1])
        m = report["align_timestamp"]["matched_pairs"]
        print(f"[timestamp] matched={m} largest MAE: {worst[0]} = {worst[1]}")
    elif want_ts:
        print(f"[timestamp] matched={report.get('align_timestamp', {}).get('matched_pairs', 0)} (no MAE)")

    if want_rel and report.get("align_relative", {}).get("mae_over_matched"):
        mae = report["align_relative"]["mae_over_matched"]
        worst = max(mae.items(), key=lambda kv: kv[1])
        m = report["align_relative"]["matched_pairs"]
        print(f"[relative] matched={m} largest MAE: {worst[0]} = {worst[1]}")
    elif want_rel:
        print(f"[relative] matched={report.get('align_relative', {}).get('matched_pairs', 0)} (no MAE)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
