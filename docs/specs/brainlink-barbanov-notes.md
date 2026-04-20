# Разбор: BaranovArtyom / brainlink_parser_linux

Источник (локальный клон): `docs/specs/vendor/brainlink_parser_linux/`  
Upstream: https://github.com/BaranovArtyom/brainlink_parser_linux

## Зависимости

- BLE: `bleak`, тот же **NUS notify UUID** `6e400003-b5a3-f393-e0a9-e50e24dcca9e`, что и в нашем `brainlink-ble-probe.json`.
- Пример: `brainlink_ble_example.py` — `ADR` из `.env`, колбэки `onEEG` / `onEXT`.

## Модель кадра (отличается от наших скриптов)

Файл `brainlink_parser_linux.py`, метод `_extract_packet`:

1. Синхронизация: выкинуть байты до пары **`0xAA 0xAA`**.
2. Третий байт — **`length`** длины полезной нагрузки.
3. Длина всего пакета: **`3 + length + 1`** (синхрон + длина + payload + один байт checksum).
4. Проверка: **`(sum(payload) + checksum) & 0xFF == 0xFF`**.

То есть это **не** разрезание по суффиксу `23 23`, как в `brainlink_frame_splitter.py`, и **не** фиксированные 50-байтные блоки `20 02` из `brainlink_frame_decoder.py`. В одном BLE-notify у вас могут идти **несколько** таких пакетов подряд, либо поток смешанного формата — это нужно проверить на **одних и тех же сырых байтах** (сравнить hex с COM3 и с BLE).

## Семантика полей (внутри payload после извлечения)

Короткие TLV-подобные записи (`code < 0x80`: один байт значения):

| code | Поле |
|------|------|
| `0x02` | signal |
| `0x04` | attention |
| `0x05` | meditation |

Длинные (`code >= 0x80`, далее `size`, затем `block`):

| code + данные | Смысл |
|---------------|--------|
| `0x80`, 2 байта | raw int16 BE → `raw_callback` |
| `0x83`, 24 байта | 8 полос по **3 байта big-endian** каждая → delta … highGamma |

Расширение / гиро в `_handle_extend`: блок **6 байт** → три int16 BE (гиро); также батарея, температура, пульс по эвристикам длины.

## Практический вывод для NeuroSync Pro

1. **COM3 + байты** — имеет смысл прогнать через `BrainLinkParser.parse()` из этого репо и сравнить с экраном / с официальным `.pyd` (если подключите Macrotellect).
2. **BLE-capture** (`brainlink-raw-capture.jsonl`) — попробовать **скормить тем же `parse()`** поток байт целиком: если кадры совместимы, колбэки начнут сходиться; если нет — на проводе два слоя (UART-оболочка `2323` поверх ThinkGear-пакетов) и нужен **двухэтапный** разбор.
3. Имеет смысл завести маленький скрипт-сравнение (отдельная задача): одна сессия → raw hex → Baranov parser vs наш `brainlink_frame_decoder`.
