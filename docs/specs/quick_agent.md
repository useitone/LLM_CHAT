# Quick agent (эвристики) — быстрый старт

Цель: обкатать “наш фреймворк” без выбора LLM/фреймворка.

Агент:
- читает последние `observation` из `docs/specs/sessions/*.jsonl`
- периодически отправляет `program.set_spec` в UI
- поднимает endpoint под `Sink URL` (можно подключить сразу)

## Запуск

1) В UI включите:
- `Запись: Вкл` (чтобы создавался JSONL в `docs/specs/sessions/`)
- `Agent API :8765`

2) Запустите агента:

```bash
python tools/quick_agent.py
```

3) (Опционально) В UI в правой панели “Программатор” установите:

`Sink URL` = `http://127.0.0.1:8766/v1/ui_event`

## Переменные окружения (опционально)

- `NSP_UI_AGENT_API_URL` (по умолчанию `http://127.0.0.1:8765/v1/event`)
- `NSP_SESSION_DIR` (по умолчанию `docs/specs/sessions`)

## Что делает агент

Каждые ~10 секунд:
- берёт последнюю запись `type="observation"`
- выбирает `spec` по простой эвристике (alpha/beta/theta + noise)
- отправляет `program.set_spec`

Это не “умный агент”, а проверка контура:
observe → decide → act → observe.

