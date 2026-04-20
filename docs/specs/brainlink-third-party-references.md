# BrainLink Pro — сторонние репозитории и эталоны

Сводка ссылок для **сверки протокола** и полей EEG. В основной git-истории чужой код **не коммитим**; для разбора можно сделать локальный клон в `docs/specs/vendor/` (см. [`vendor/README.md`](vendor/README.md), каталог `brainlink_parser_linux/` в `.gitignore`).

## Официальные (Macrotellect)

| Репозиторий | Назначение | Открытый парсер? |
|-------------|------------|-------------------|
| [Macrotellect/BrainLinkParser-Python](https://github.com/Macrotellect/BrainLinkParser-Python) | BrainLink **Pro** / Lite; пример через **виртуальный COM** (`CushySerial`, 115200) и модуль `BrainLinkParser`; колбэки EEG / extend / gyro | Нет: `BrainLinkParser.pyd` (Windows), `.so` (macOS) |
| [Macrotellect/BrainLinkDualSDK_Windows](https://github.com/Macrotellect/BrainLinkDualSDK_Windows) | **BrainLink Dual** (два канала), Windows, Python-демо; `BrainLinkDualParser.dll` | Нет: DLL |

## Сообщество (читаемый Python)

| Репозиторий | Назначение | Приоритет для байт/BLE |
|-------------|------------|-------------------------|
| [BaranovArtyom/brainlink_parser_linux](https://github.com/BaranovArtyom/brainlink_parser_linux) | `brainlink_ble_example.py`, `brainlink_parser_linux.py`, примеры графиков | **Высокий** — смотреть исходник первым при сверке с нашим BLE-потоком; разбор: [brainlink-barbanov-notes.md](brainlink-barbanov-notes.md) |

## Другие продукты / крупные проекты

| Репозиторий | Назначение | Замечание |
|-------------|------------|-----------|
| [AleksanderDudek/brainlink-bluetooth-macos-integration](https://github.com/AleksanderDudek/brainlink-bluetooth-macos-integration) | **BrainAccess HALO** (не Pro), BLE на macOS (`bleak`), FastAPI + WebSocket + дашборд | Лицензия **AGPL-3.0**; протокол HALO не обязан совпадать с BrainLink Pro |
| [Lukelaitw/114-1_BME_LAB_Final_Project_G5](https://github.com/Lukelaitw/114-1_BME_LAB_Final_Project_G5) | Учебный BCI-проект; BrainLink через **serial** и вендорный парсер (вложенный BrainLinkParser-Python) | Низкий приоритет для разбора байт; полезен как пример интеграции с игрой |

## Рекомендуемая стратегия сверки

1. **(A) COM + официальный модуль** — если в Windows после сопряжения появляется **выходной** виртуальный COM-порт (см. FAQ в репозитории Macrotellect), прогнать тот же поток через `BrainLinkParser.parse(...)` и сравнить **attention / meditation / полосы** с экраном или логом. Это эталон **чисел**, не исходника протокола.

2. **(B) Community BLE** — открыть исходники [BaranovArtyom/brainlink_parser_linux](https://github.com/BaranovArtyom/brainlink_parser_linux) и сопоставить разбор кадров с нашим пайплайном.

3. **(C) Локальный пайплайн в этом репозитории** — скрипты `scripts/brainlink_probe.py`, `brainlink_stream_capture.py`, `brainlink_frame_splitter.py`, `brainlink_frame_decoder.py`; артефакты в `docs/specs/brainlink-*.json/jsonl`. После сверки по (A) или (B) править смещения в `brainlink_frame_decoder.py` при необходимости.

## Связанные файлы в NeuroSync Pro

- Заметки по железу и BLE: [brainlink-protocol-notes.md](brainlink-protocol-notes.md)
- Отчёт декодера: [brainlink-frame-decode-report.json](brainlink-frame-decode-report.json)
- Разбор BaranovArtyom: [brainlink-barbanov-notes.md](brainlink-barbanov-notes.md)
