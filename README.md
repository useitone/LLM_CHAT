# NeuroSync Pro — материалы и планирование

Репозиторий для сбора исходных материалов (переписка, руководства, скриншоты) перед разработкой кроссплатформенного приложения **NeuroSync Pro** (ЭЭГ + генератор частот + AI + светомузыка).

**Стек целевой разработки (по текущему плану):** Windows + Python; кроссплатформенность закладывается архитектурно.

**Исходники на GitHub:** https://github.com/useitone/LLM_CHAT

**Продолжить после нового чата:** [`docs/specs/handoff-todo.md`](docs/specs/handoff-todo.md) — нить разговора, решения и чеклисты.

## Текущий фокус разработки

Приоритет — **ветка A: MVP** (UI, сценарий медитации, аудио) на **данных по BLE**: PoC `neurosync-pro meditation --ble-address …` (парсер `neurosync_pro.eeg`, поток `neurosync_pro.eeg.ble_stream`, фоновый поток в `neurosync_pro.ui.ble_thread`), плюс сырой захват `scripts/brainlink_stream_capture.py`. **Строгая сверка COM↔BLE** (эталон Macrotellect, параллельный захват, калибровка по лагу) **отложена**: на одном ПК стабильная параллельность не подтверждена; второй BT USB‑адаптер **не подключился** — возвращаться к ветке B при отдельной среде или рабочем втором адаптере.

## Структура

| Папка | Назначение |
|-------|------------|
| `docs/alisa-conversation/` | Экспорт переписки с AI Алиса (текст, Markdown или PDF) |
| `docs/neuroexperimenter/` | Руководство пользователя NeuroExperimenter, скриншоты, заметки |
| `docs/sweep-tone-generator/` | Скриншоты и описания приложения Sweep Tone Generator |
| `docs/specs/` | Черновики ТЗ, приоритеты продукта, API, спецификации устройств |
| `src/neurosync_pro/` | Код приложения (MVP): модули `eeg`, `audio`, точка входа CLI |

**Установка каркаса для разработки** (из корня репозитория, желательно в venv):

```bash
pip install -e ".[dev]"
neurosync-pro --help
pip install -e ".[gui]"   # PySide6: eeg-replay, meditation
neurosync-pro meditation --ble-address "C0:E2:FC:2D:AC:10" --session-log docs/specs/meditation-session.jsonl
neurosync-pro meditation --input docs/specs/one-session-ble.jsonl
neurosync-pro decode --input docs/specs/brainlink-raw-capture.jsonl
neurosync-pro compare
neurosync-pro concurrent-capture --address "C0:E2:FC:2D:AC:10" --stem docs/specs/brainlink-concurrent-session
neurosync-pro agent-serve
python scripts/brainlink_verify_one_session.py --summary
```

### Сейчас в репозитории

- **`docs/alisa-conversation/alisa-neurosync-brief.md`** — переписка с AI Алиса (концепция и ТЗ).
- **`docs/neuroexperimenter/NeuroExperimenter Users Guide.docx`** — официальное руководство пользователя NeuroExperimenter (текст и встроенные иллюстрации).
- **`docs/neuroexperimenter/screenshots/`** — скриншоты интерфейса (`nex59_2_0.jpg` … `nex59_2_8.jpg`).
- **`docs/sweep-tone-generator/SweepToneGenerator.md`** — конспект возможностей Tone Generator PRO (Android); скрины в **`docs/sweep-tone-generator/screenshots/`** (`tone-generator-pro-android-*.png`).
- **`docs/specs/product-priorities.md`** — зафиксированные приоритеты: медитация/концентрация, графики ЭЭГ, развитие аудиомодуля по мотивам Sweep Tone Generator и агенты.
- **`docs/specs/handoff-todo.md`** — TODO на будущее и напоминания после обновления чата.

## Что положить сюда

1. **Переписка с Алисой** — любой удобный формат; имя вроде `alisa-neurosync-brief.md` упростит поиск.
2. **NeuroExperimenter** — руководство в Word (`NeuroExperimenter Users Guide.docx`) и при необходимости дополнительные скрины в `docs/neuroexperimenter/screenshots/`.
3. **Sweep Tone Generator** — скрины + краткое текстовое описание режимов (частоты, sweep, пресеты).

После наполнения можно зафиксировать курс: MVP, приоритет устройств (например Brainlink Pro), границы первой версии.

## Лицензия

Материалы в `docs/` могут быть под разными правами; код проекта при появлении — см. корневой `LICENSE`.

## Contributors

См. [`CONTRIBUTORS.md`](CONTRIBUTORS.md).
