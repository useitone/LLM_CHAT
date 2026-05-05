from neurosync_pro.agent_runtime.chat_reply_parse import strip_md_json_fence, try_parse_ready_program_decision
from neurosync_pro.agent_runtime.contracts import Decision


def _ready(d: Decision) -> bool:
    return d.action in ("set_spec", "set_timeline", "stop")


def test_strip_md_json_fence() -> None:
    raw = "```json\n{\"a\":1}\n```"
    assert strip_md_json_fence(raw) == '{"a":1}'


def test_try_parse_fenced_at_end() -> None:
    raw = """Здравствуйте.

```json
{"action":"stop","confidence":1,"reason_code":"t"}
```
"""
    d = try_parse_ready_program_decision(raw, command_ready=_ready)
    assert d is not None
    assert d.action == "stop"


def test_try_parse_prefers_last_json_block() -> None:
    raw = """```json
{"action":"hold","confidence":1,"reason_code":"x"}
```
Теперь команда:
```json
{"action":"set_spec","spec":"off","confidence":1,"reason_code":"y"}
```
"""
    d = try_parse_ready_program_decision(raw, command_ready=_ready)
    assert d is not None
    assert d.action == "set_spec"
    assert d.spec == "off"
