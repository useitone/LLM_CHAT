import json

from neurosync_pro.agent_runtime.user_profile import load_ui_profile, save_ui_profile


def test_save_load_roundtrip(tmp_path) -> None:
    p = tmp_path / "ui_profile.json"
    save_ui_profile({"ollama_base_url": "http://x:11434", "ollama_model": "m1", "chat_auto_apply": True}, p)
    data = load_ui_profile(p)
    assert data["ollama_base_url"] == "http://x:11434"
    assert data["ollama_model"] == "m1"
    assert data["chat_auto_apply"] is True
    assert json.loads(p.read_text(encoding="utf-8"))["version"] >= 1


def test_load_missing_returns_defaults(tmp_path) -> None:
    data = load_ui_profile(tmp_path / "none.json")
    assert data["chat_free_form"] is True
    assert data["chat_agent_runtime_policy"] is False
    assert data["chat_freeflight"] is False
