# NeuroSync Pro — материалы и планирование

Репозиторий для сбора исходных материалов (переписка, руководства, скриншоты) перед разработкой кроссплатформенного приложения **NeuroSync Pro** (ЭЭГ + генератор частот + AI + светомузыка).

**Стек целевой разработки (по текущему плану):** Windows + Python; кроссплатформенность закладывается архитектурно.

## Структура

| Папка | Назначение |
|-------|------------|
| `docs/alisa-conversation/` | Экспорт переписки с AI Алиса (текст, Markdown или PDF) |
| `docs/neuroexperimenter/` | Руководство пользователя NeuroExperimenter, скриншоты, заметки |
| `docs/sweep-tone-generator/` | Скриншоты и описания приложения Sweep Tone Generator |
| `docs/specs/` | Черновики ТЗ, приоритеты продукта, API, спецификации устройств |

### Сейчас в репозитории

- **`docs/alisa-conversation/alisa-neurosync-brief.md`** — переписка с AI Алиса (концепция и ТЗ).
- **`docs/neuroexperimenter/NeuroExperimenter Users Guide.docx`** — официальное руководство пользователя NeuroExperimenter (текст и встроенные иллюстрации).
- **`docs/neuroexperimenter/screenshots/`** — скриншоты интерфейса (`nex59_2_0.jpg` … `nex59_2_8.jpg`).
- **`docs/sweep-tone-generator/SweepToneGenrator.md`** — конспект возможностей Tone Generator PRO (Android); скрины в **`docs/sweep-tone-generator/screenshots/`** (`tone-generator-pro-android-*.png`).
- **`docs/specs/product-priorities.md`** — зафиксированные приоритеты: медитация/концентрация, графики ЭЭГ, развитие аудиомодуля по мотивам Sweep Tone Generator и агенты.

## Что положить сюда

1. **Переписка с Алисой** — любой удобный формат; имя вроде `alisa-neurosync-brief.md` упростит поиск.
2. **NeuroExperimenter** — руководство в Word (`NeuroExperimenter Users Guide.docx`) и при необходимости дополнительные скрины в `docs/neuroexperimenter/screenshots/`.
3. **Sweep Tone Generator** — скрины + краткое текстовое описание режимов (частоты, sweep, пресеты).

После наполнения можно зафиксировать курс: MVP, приоритет устройств (например Brainlink Pro), границы первой версии.

## Лицензия

Материалы в `docs/` могут быть под разными правами; код проекта при появлении — см. корневой `LICENSE`.
