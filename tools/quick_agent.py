"""
Quick-start heuristic agent for NeuroSync Pro.

Goals:
- Receive UI events via HTTP (Sink URL) at /v1/ui_event (optional).
- Read latest session JSONL and use `observation` windows.
- Send program commands to UI Agent API: POST /v1/event.

This is intentionally framework-free (stdlib only) to validate the pipeline:
observe -> decide -> act -> observe.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


DEFAULT_UI_EVENT_HOST = "127.0.0.1"
DEFAULT_UI_EVENT_PORT = 8766

DEFAULT_UI_AGENT_API_URL = "http://127.0.0.1:8765/v1/event"
DEFAULT_SESSION_DIR = Path.cwd() / "docs" / "specs" / "sessions"


def _post_json(url: str, obj: dict[str, Any], *, timeout_s: float = 2.0) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as _resp:
        pass


def send_set_spec(ui_agent_api_url: str, spec: str) -> None:
    _post_json(ui_agent_api_url, {"topic": "program.set_spec", "payload": {"spec": str(spec)}})


def send_stop(ui_agent_api_url: str) -> None:
    _post_json(ui_agent_api_url, {"topic": "program.stop", "payload": {}})


@dataclass
class Latest:
    lock: threading.Lock
    last_observation: dict[str, Any] | None = None
    last_program_status: dict[str, Any] | None = None


class _Handler(BaseHTTPRequestHandler):
    latest: Latest | None = None

    def log_message(self, _format: str, *_args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in ("/v1/ui_event", "/v1/ui_event/"):
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return
        topic = str(body.get("topic") or "")
        payload = body.get("payload")
        lat = _Handler.latest
        if lat is not None:
            with lat.lock:
                if topic == "program.status":
                    lat.last_program_status = payload if isinstance(payload, dict) else {"raw": payload}
        self.send_response(204)
        self.end_headers()


def _pick_latest_session_file(session_dir: Path) -> Path | None:
    if not session_dir.exists():
        return None
    files = sorted(session_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _tail_observations(latest: Latest, session_dir: Path, *, poll_s: float = 0.5) -> None:
    path: Path | None = None
    fp = None
    pos = 0
    while True:
        try:
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
                fp = path.open("r", encoding="utf-8")
                pos = 0

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
            with latest.lock:
                latest.last_observation = obj
        except Exception:
            time.sleep(poll_s)


def _get_mean(obs: dict[str, Any], path: list[str]) -> float | None:
    cur: Any = obs
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    if isinstance(cur, dict) and "mean" in cur:
        try:
            return float(cur["mean"])
        except (TypeError, ValueError):
            return None
    return None


def decide_spec(obs: dict[str, Any]) -> str:
    """
    Very simple heuristic:
    - If attention mean low -> beta-ish beat (15 Hz) + brown noise a bit louder.
    - If meditation mean high -> theta-ish beat (6 Hz) + pink noise lower.
    - Otherwise alpha-ish beat (10 Hz) + pink noise.
    """
    att = _get_mean(obs, ["eeg", "attention"])  # 0..100
    med = _get_mean(obs, ["eeg", "meditation"])  # 0..100

    # Defaults
    carrier = 200.0
    beat = 10.0
    noise_color = "pink"
    noise_vol = 0.06
    tone_amp = 0.55

    if att is not None and att < 40:
        beat = 15.0
        noise_color = "brown"
        noise_vol = 0.10
        tone_amp = 0.60
    elif med is not None and med > 60:
        beat = 6.0
        noise_color = "pink"
        noise_vol = 0.04
        tone_amp = 0.50

    return f"{carrier:.0f}+{beat:.0f}/{tone_amp:.2f} {noise_color}/{noise_vol:.2f}"


def _control_loop(latest: Latest, ui_agent_api_url: str, *, min_interval_s: float = 10.0) -> None:
    last_sent_at = 0.0
    last_sent_spec = ""
    while True:
        time.sleep(0.25)
        with latest.lock:
            obs = latest.last_observation
        if not isinstance(obs, dict):
            continue
        now = time.monotonic()
        if now - last_sent_at < min_interval_s:
            continue
        spec = decide_spec(obs)
        if spec == last_sent_spec:
            last_sent_at = now
            continue
        try:
            send_set_spec(ui_agent_api_url, spec)
            last_sent_spec = spec
            last_sent_at = now
            print(f"[agent] set_spec: {spec}")
        except Exception as exc:
            print(f"[agent] send failed: {exc}")


def main() -> None:
    ui_agent_api_url = os.environ.get("NSP_UI_AGENT_API_URL", DEFAULT_UI_AGENT_API_URL)
    session_dir = Path(os.environ.get("NSP_SESSION_DIR", str(DEFAULT_SESSION_DIR)))

    latest = Latest(lock=threading.Lock())
    _Handler.latest = latest

    server = ThreadingHTTPServer((DEFAULT_UI_EVENT_HOST, DEFAULT_UI_EVENT_PORT), _Handler)
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    print(f"[agent] sink listening at http://{DEFAULT_UI_EVENT_HOST}:{DEFAULT_UI_EVENT_PORT}/v1/ui_event")
    print(f"[agent] UI Agent API: {ui_agent_api_url}")
    print(f"[agent] session dir: {session_dir}")

    threading.Thread(target=_tail_observations, args=(latest, session_dir), daemon=True).start()
    _control_loop(latest, ui_agent_api_url)


if __name__ == "__main__":
    main()

