from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from neurosync_pro.agent_runtime.loop import RuntimeState, iter_observations, step_observation
from neurosync_pro.agent_runtime.providers import CloudProvider, LocalProvider, ModelProvider


def _pick_latest_session_file(session_dir: Path) -> Path | None:
    if not session_dir.exists():
        return None
    files = sorted(session_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _build_provider(mode: str, *, local_model: str | None = None) -> ModelProvider | None:
    if mode == "local":
        model = (local_model or "").strip() or os.environ.get("NSP_LOCAL_MODEL", "next2-local")
        return LocalProvider(
            base_url=os.environ.get("NSP_LOCAL_BASE_URL", "http://127.0.0.1:11434"),
            model=model,
            timeout_s=float(os.environ.get("NSP_MODEL_TIMEOUT_S", "15")),
        )
    if mode == "cloud":
        api_key = os.environ.get("NSP_CLOUD_API_KEY", "").strip()
        if not api_key:
            return None
        return CloudProvider(
            base_url=os.environ.get("NSP_CLOUD_BASE_URL", "https://api.openai.com/v1"),
            model=os.environ.get("NSP_CLOUD_MODEL", "gpt-4o-mini"),
            api_key=api_key,
            timeout_s=float(os.environ.get("NSP_MODEL_TIMEOUT_S", "20")),
        )
    return None


def run_once(
    *,
    mode: str,
    session_file: Path,
    ui_agent_api_url: str,
    cooldown_s: float,
    send_actions: bool = True,
    local_model: str | None = None,
) -> list[dict[str, Any]]:
    provider = _build_provider(mode, local_model=local_model)
    state = RuntimeState()
    out: list[dict[str, Any]] = []
    for obs in iter_observations(session_file):
        out.append(
            step_observation(
                obs,
                mode=mode,
                provider=provider,
                state=state,
                cooldown_s=cooldown_s,
                ui_agent_api_url=ui_agent_api_url,
                send_actions=send_actions,
            )
        )
    return out


def run_follow(
    *,
    mode: str,
    session_dir: Path,
    session_file: Path | None,
    ui_agent_api_url: str,
    cooldown_s: float,
    send_actions: bool,
    local_model: str | None,
    replay: bool,
    verbose: bool,
    poll_s: float,
) -> None:
    provider = _build_provider(mode, local_model=local_model)
    state = RuntimeState()

    path: Path | None = None
    fp = None
    pos = 0

    def _open_path(p: Path, *, from_start: bool) -> tuple[Any, int]:
        f = p.open("r", encoding="utf-8")
        if from_start:
            return f, 0
        f.seek(0, os.SEEK_END)
        return f, f.tell()

    print(
        f"[agent_runtime] follow poll={poll_s}s replay={replay} "
        f"session_dir={session_dir} fixed_file={session_file}",
        file=sys.stderr,
    )

    while True:
        try:
            cur: Path | None
            if session_file is not None:
                if not session_file.exists():
                    time.sleep(poll_s)
                    continue
                cur = session_file
            else:
                cur = _pick_latest_session_file(session_dir)
                if cur is None:
                    time.sleep(poll_s)
                    continue

            if path != cur:
                path = cur
                if fp is not None:
                    try:
                        fp.close()
                    except OSError:
                        pass
                fp, pos = _open_path(path, from_start=replay)
                replay = False  # only first open replays if requested
                if verbose:
                    print(f"[agent_runtime] tail file={path}", file=sys.stderr)

            assert fp is not None
            fp.seek(pos)
            line = fp.readline()
            if not line:
                pos = fp.tell()
                time.sleep(poll_s)
                continue
            pos = fp.tell()
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "observation":
                continue

            row = step_observation(
                obj,
                mode=mode,
                provider=provider,
                state=state,
                cooldown_s=cooldown_s,
                ui_agent_api_url=ui_agent_api_url,
                send_actions=send_actions,
            )
            if verbose:
                print(json.dumps(row, ensure_ascii=False), flush=True)
        except KeyboardInterrupt:
            print("[agent_runtime] stopped", file=sys.stderr)
            break
        except Exception as exc:
            print(f"[agent_runtime] error: {exc}", file=sys.stderr)
            time.sleep(poll_s)

    if fp is not None:
        try:
            fp.close()
        except OSError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="NeuroSync runtime: cloud/local/heuristic")
    parser.add_argument("--mode", choices=["cloud", "local", "heuristic"], default="heuristic")
    parser.add_argument("--session-file", default="", help="Path to session jsonl. Empty -> latest in session-dir")
    parser.add_argument("--session-dir", default=os.environ.get("NSP_SESSION_DIR", "docs/specs/sessions"))
    parser.add_argument("--ui-agent-api-url", default=os.environ.get("NSP_UI_AGENT_API_URL", "http://127.0.0.1:8765/v1/event"))
    parser.add_argument("--cooldown-s", type=float, default=float(os.environ.get("NSP_COOLDOWN_S", "12")))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--local-model",
        default="",
        help="Ollama model name (overrides NSP_LOCAL_MODEL), e.g. deepseek-v3.1:671b-cloud",
    )
    parser.add_argument(
        "--follow",
        action="store_true",
        help="Tail session JSONL for new observation lines (live loop). Ctrl+C to stop.",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="With --follow: read existing file from start on first open (default: only new lines).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Print one JSON line per decision")
    args = parser.parse_args()
    local_model_arg = args.local_model.strip() or None
    poll_s = float(os.environ.get("NSP_TAIL_POLL_S", "0.5"))

    if args.follow:
        fixed_sf = Path(args.session_file) if str(args.session_file).strip() else None
        run_follow(
            mode=args.mode,
            session_dir=Path(args.session_dir),
            session_file=fixed_sf,
            ui_agent_api_url=args.ui_agent_api_url,
            cooldown_s=args.cooldown_s,
            send_actions=not args.dry_run,
            local_model=local_model_arg,
            replay=args.replay,
            verbose=args.verbose,
            poll_s=poll_s,
        )
        return

    session_file = Path(args.session_file) if args.session_file else _pick_latest_session_file(Path(args.session_dir))
    if session_file is None or not session_file.exists():
        raise SystemExit("Session file not found. Pass --session-file or NSP_SESSION_DIR.")

    if args.dry_run:
        # Disable outbound actions but keep decision loop identical.
        results = run_once(
            mode=args.mode,
            session_file=session_file,
            ui_agent_api_url=args.ui_agent_api_url,
            cooldown_s=args.cooldown_s,
            send_actions=False,
            local_model=local_model_arg,
        )
        print(json.dumps({"count": len(results), "last": results[-1] if results else None}, ensure_ascii=False, indent=2))
        return

    results = run_once(
        mode=args.mode,
        session_file=session_file,
        ui_agent_api_url=args.ui_agent_api_url,
        cooldown_s=args.cooldown_s,
        local_model=local_model_arg,
    )
    print(json.dumps({"count": len(results), "last": results[-1] if results else None}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
