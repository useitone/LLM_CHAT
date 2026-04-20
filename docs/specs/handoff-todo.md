# NeuroSync Pro — нить разговора (TODO после обновления чата)

Этот файл держит **контекст и следующие шаги**, чтобы после нового чата в Cursor не начинать с нуля. Обновляйте по мере прогресса.

## Зафиксированные решения (не забыть)

| Тема | Решение |
|------|---------|
| Название | NeuroSync Pro (в переписке с Алисой встречалось и «NeuroSync Bridge» — при публикации выбрать одно) |
| Стек разработки | **Windows + Python** первым; GUI — PySide6/PyQt; кроссплатформенность закладывать архитектурно |
| Железо под рукой | Windows 10, нейрогарнитура **Brainlink Pro** |
| База продукта | **Медитация и концентрация** — главный сценарий; формулы/параметры — поддержка |
| NeuroExperimenter | В приоритете **графики в реальном времени** и **ручная отладка** пользователем |
| Sweep / Tone Generator | **Северный ориентир** — мощный эталон (Tone Generator PRO); со временем стремиться к полноте функций, **сразу проектировать под AI-агентов** (API, лимиты, логи) |
| Лицензия кода | **MIT**, репозиторий открытый, публикация на GitHub |
| Удалённый репозиторий | https://github.com/useitone/LLM_CHAT |
| Ветка разработки (апр. 2026) | **A** — MVP на **BLE** (UI, медитация, аудио). **B** (строгая сверка COM↔BLE, калибровка) — **отложена** до отдельного железа/среды; второй BT свисток не заработал |

Подробнее: **`product-priorities.md`**.

## Что уже есть в репозитории

- Переписка с Алисой: `docs/alisa-conversation/alisa-neurosync-brief.md`
- NeuroExperimenter: `docs/neuroexperimenter/NeuroExperimenter Users Guide.docx`, скрины `docs/neuroexperimenter/screenshots/nex59_2_*.jpg`
- Tone Generator PRO: `docs/sweep-tone-generator/SweepToneGenerator.md`, скрины `docs/sweep-tone-generator/screenshots/*.png`
- Приоритеты: `docs/specs/product-priorities.md`
- Сторонние GitHub-эталоны по BrainLink (ссылки, без клонирования): `docs/specs/brainlink-third-party-references.md`
- Разбор парсера BaranovArtyom (модель пакетов vs наши скрипты): `docs/specs/brainlink-barbanov-notes.md`; локальный клон — см. `docs/specs/vendor/README.md` (каталог в `.gitignore`).
- Захват сырого потока с **COM** (без парсера): `scripts/brainlink_com_capture.py` → `docs/specs/brainlink-com-raw-capture.jsonl`.
- COM + **официальный** Macrotellect `BrainLinkParser.pyd`: `scripts/brainlink_com_macrotellect.py` → `docs/specs/brainlink-com-macrotellect.jsonl` (нужен ручной копи `.pyd`, см. `docs/specs/vendor/macrotellect_brainlink_parser/README.md`).
- **Один прогон COM+BLE** (перекрывающийся UTC для compare): `scripts/brainlink_concurrent_capture.py` — см. `brainlink-protocol-notes.md` §15; оркестратор (chdir + compare + сводка в консоль): `scripts/brainlink_verify_one_session.py` — §16.
- **Live BLE в PoC медитации:** `neurosync-pro meditation --ble-address …` (опции `--ble-init-hex`, `--ble-duration`, `--session-log`); модули `neurosync_pro.eeg.live_decode`, `neurosync_pro.eeg.ble_stream`, `neurosync_pro.ui.ble_thread`; биофидбэк-тон в UI (чекбокс).
- **Зафиксированный статус EEG MVP:** `docs/specs/eeg-mvp-status.md` (архитектура, команды, формат логов, критерии стабильности).

## TODO — пользователю

- [x] Подписка Cursor оплачена — активная разработка через агента возобновлена.
- [x] Декодер фреймов Brainlink: `scripts/brainlink_frame_decoder.py` (отчёт: `docs/specs/brainlink-frame-decode-report.json`).
- [x] Переименован файл `SweepToneGenrator.md` → `SweepToneGenerator.md`; ссылки синхронизированы.
- [ ] Уточнить у производителя / документации **протокол Brainlink Pro** (BLE/COM, частота, формат пакетов) — положить в `docs/specs/` как отдельный файл.
  - Черновик создан: `docs/specs/brainlink-protocol-notes.md`.
  - Сводка сторонних репо и стратегия сверки: `docs/specs/brainlink-third-party-references.md` (шаг 1: выбрать канал **COM + официальный .pyd** или **открытый BLE-исходник** из списка).
- [x] Выбрана лицензия кода: **MIT** (open-source, публикация на GitHub).
- [x] Добавлен файл `LICENSE` (MIT template) в корень репозитория.
- [ ] Настроить git-автора (это не "глобальные переменные", а профиль Git):
  - глобально для всех репозиториев: `git config --global user.name "Ваше Имя"` и `git config --global user.email "you@example.com"`;
  - или только для этого проекта: `git config user.name "Ваше Имя"` и `git config user.email "you@example.com"`.

## TODO — следующая сессия разработки (MVP-код, ветка A)

- [x] **MVP на BLE:** живой BLE → парсер → `meditation` PoC + шина (`eeg.metrics`); кнопки Старт/Стоп BLE, автостарт при передаче `--ble-address` из CLI.
- [x] **Аудио в сессии (черновик):** чекбокс «Тон обратной связи» — краткий синус, частота ~ Attention, громкость ~ Meditation (`neurosync_pro.audio.engine`); смена фазы по-прежнему со звуком.
- [x] **Критерий готовности чернового MVP:** проверено на железе ~10–15 мин: стабильный поток, график, скан в UI, лог‑сессии, кнопки Stop/Close отрабатывают; при необходимости — дальнейшие UX‑улучшения.
- [x] Опциональный **лог сессии JSONL** (`--session-log`, строки `type=eeg` как в дампах для replay).
- [x] Создать структуру Python-проекта: `pyproject.toml`, пакет `src/neurosync_pro/` (`eeg/`, `audio/`, CLI `neurosync-pro`). Установка: `pip install -e ".[dev]"` из корня репозитория.
- [x] Модуль **ЭЭГ** (разбор кадров): `neurosync_pro.eeg.protocol`, тесты `tests/test_eeg_protocol.py`; визуализация — `pip install -e ".[gui]"` → `neurosync-pro eeg-replay --input …jsonl`.
- [x] Модуль **аудио** (MVP): `neurosync_pro.audio` — синус, linear sweep, WAV (`write_wav_pcm16_mono`).
- [x] **Событийная шина** (`neurosync_pro.bus`) и **API агентов** (`neurosync-pro agent-serve`, POST `/v1/event` в `neurosync_pro.agent.server`); в PoC медитации — чекбокс включения API.
- [ ] Логирование сессий (SQLite + экспорт CSV/EDF — по мере готовности библиотек).
- [x] Сценарий «медитация/концентрация» (PoC): `neurosync-pro meditation [--input path.jsonl | --ble-address MAC …]`.

## TODO — отложено (ветка B: сверка COM↔BLE)

Не блокирует MVP. Вернуться, когда будет стабильный **параллельный** захват (два хоста или рабочий второй BT‑адаптер / иная среда).

- [ ] Сырой COM при необходимости: `brainlink_com_capture.py` → `brainlink-com-raw-capture.jsonl`.
- [ ] Эталон Macrotellect: `brainlink_com_macrotellect.py` → дамп JSONL.
- [ ] Сверка: `brainlink_concurrent_capture.py` → `brainlink_compare_macrotellect_ble.py` (`align_timestamp`); при расхождениях — `neurosync_pro.eeg.protocol` / скрипты; BaranovArtyom — опционально (`brainlink-barbanov-notes.md`).
- [ ] Починить при желании оркестратор: пустой лог ошибок BLE в `session.json`, `started_utc` через `monotonic` в `brainlink_concurrent_capture.py` (см. `brainlink-protocol-notes.md` §15–16).

## TODO — позже (после MVP)

- [ ] Локальная малая модель (≤16 МБ) + политика безопасности частот/громкости.
- [ ] Облачный LLM (чат, не чаще чем позволяет задержка; агрегированные фичи ЭЭГ, не сырые данные без согласия).
- [ ] Светомузыка (Hue / Yeelight / DMX) — один тип устройств в первой итерации.
- [ ] Установщик Windows, затем macOS/Linux.

## Открытые вопросы (заполнить по мере ясности)

- Точный список **MVP-экранов** по скринам NeuroExperimenter (сопоставить с `nex59_2_*.jpg`).
- На GitHub сейчас **монорепо** `useitone/LLM_CHAT` (`docs/` + `scripts/` + `src/`). Отдельный репозиторий только под код — по желанию перед первым релизом приложения.

---

**Для ассистента в новом чате:** сначала прочитать этот файл и `product-priorities.md`, затем README (блок «Текущий фокус») и релевантные документы в `docs/`. Сейчас в приоритете **ветка A** (BLE‑MVP), не ветка B.
