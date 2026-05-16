import json

import pytest

from neurosync_pro.light.auto_rules import (
    get_auto_rules_from_env,
    parse_auto_rules_document,
    rgb_from_auto_rules,
)


def test_parse_list_meditation_first() -> None:
    doc = json.loads(
        '[{"metric": "meditation", "op": ">=", "value": 70, "rgb": [1,2,3]},'
        '{"metric": "attention", "op": ">=", "value": 70, "rgb": [4,5,6]}]'
    )
    rules, idle = parse_auto_rules_document(doc)
    assert idle == (24, 28, 36)
    assert rgb_from_auto_rules(80, 85, rules, idle) == (1, 2, 3)
    assert rgb_from_auto_rules(80, 50, rules, idle) == (4, 5, 6)
    assert rgb_from_auto_rules(50, 50, rules, idle) == idle


def test_op_gt() -> None:
    rules, idle = parse_auto_rules_document(
        [{"metric": "attention", "op": ">", "value": 70, "rgb": [9, 9, 9]}]
    )
    assert rgb_from_auto_rules(70, 0, rules, idle) == idle
    assert rgb_from_auto_rules(71, 0, rules, idle) == (9, 9, 9)


def test_idle_in_root() -> None:
    doc = {"idle": [10, 20, 30], "rules": [{"metric": "meditation", "op": ">=", "value": 1, "rgb": [1, 1, 1]}]}
    rules, idle = parse_auto_rules_document(doc)
    assert idle == (10, 20, 30)
    assert rgb_from_auto_rules(0, 0, rules, idle) == (10, 20, 30)


def test_get_auto_rules_from_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "r.json"
    p.write_text(
        '{"idle": [0,0,0], "rules": [{"metric": "meditation", "op": ">=", "value": 50, "rgb": [255,0,0]}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("NSP_LIGHT_AUTO_RULES_PATH", str(p))
    import neurosync_pro.light.auto_rules as ar

    ar._rules_file_cache = None  # noqa: SLF001
    got = get_auto_rules_from_env()
    assert got is not None
    rules, idle = got
    assert idle == (0, 0, 0)
    assert rgb_from_auto_rules(0, 60, rules, idle) == (255, 0, 0)
