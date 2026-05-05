from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from neurosync_pro.agent_runtime.loop import RuntimeState, apply_cooldown, decide, iter_observations


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate runtime policy on observation JSONL")
    parser.add_argument("session_file", help="Path to session jsonl")
    parser.add_argument("--mode", choices=["heuristic"], default="heuristic")
    parser.add_argument("--cooldown-s", type=float, default=12.0)
    args = parser.parse_args()

    path = Path(args.session_file)
    if not path.exists():
        raise SystemExit("session_file does not exist")

    state = RuntimeState()
    total = 0
    actions: dict[str, int] = {"set_spec": 0, "hold": 0, "stop": 0}
    switches = 0
    prev_spec = ""
    latencies_ms: list[float] = []

    for obs in iter_observations(path):
        t0 = time.perf_counter()
        decision = decide(obs=obs, state=state, mode=args.mode, provider=None)
        decision = apply_cooldown(decision, state, cooldown_s=args.cooldown_s)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        total += 1
        actions[decision.action] = actions.get(decision.action, 0) + 1
        if decision.action == "set_spec" and decision.spec:
            if prev_spec and prev_spec != decision.spec:
                switches += 1
            prev_spec = decision.spec
            state.last_sent_spec = decision.spec
            state.last_sent_at = time.monotonic()

    if total == 0:
        print("No observation events found.")
        return

    lat_sorted = sorted(latencies_ms)
    p95_idx = min(len(lat_sorted) - 1, int(0.95 * len(lat_sorted)))
    report: dict[str, Any] = {
        "total_observations": total,
        "actions": actions,
        "hold_rate": round(actions.get("hold", 0) / total, 4),
        "switch_rate": round(switches / max(1, actions.get("set_spec", 0)), 4),
        "latency_ms": {
            "mean": round(sum(latencies_ms) / len(latencies_ms), 3),
            "p95": round(lat_sorted[p95_idx], 3),
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
