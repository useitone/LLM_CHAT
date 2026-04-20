# Brainlink Pro — заметки по протоколу и подключению

Черновик для фиксации проверенных технических параметров устройства перед реализацией модуля ЭЭГ.

## Статус

- Текущий статус: в работе
- Владелец: пользователь
- Последнее обновление: 2026-04-20
- Последний probe-отчёт: `docs/specs/brainlink-ble-probe.json` (2026-04-19)
- Эталон COM (Macrotellect `.pyd`): `docs/specs/brainlink-com-macrotellect.jsonl` — захват выполнен (порядка 15k событий: `eeg`, `extend`, `gyro`, `raw`).
- Сторонние эталоны (GitHub, без вендоринга кода): [`brainlink-third-party-references.md`](brainlink-third-party-references.md)

## 1) Канал подключения

- Транспорт: [x] BLE  [x] COM/Serial  [ ] Другое
- ОС проверки: Windows 10
- Приложение/инструмент проверки: `python scripts/brainlink_probe.py` (`bleak`)
- Итог: устройство обнаруживается и успешно подключается по BLE.

## 2) Параметры потока данных

- Частота отправки пакетов (Hz): уточняется (по текущему захвату 307 записей за сессию, нужно сверить с фактической длительностью запуска).
- Размер пакета (байт): в текущем `raw-capture` каждая запись имеет `len=505` (несколько логических кадров в одном notify).
- Логические кадры (по `brainlink_frame_decoder.py` на одном захвате): короткие `aaaa048002…2323` (10 байт), EEG `aaaa2002…2323` (50 байт на кадр), гиро `aaaa0703…2323` (14 байт).
- Состав пакета (поля): короткие и гиро разобраны по размеру; поля полного EEG — по смещениям pybrainlink; **эталон attention/meditation/полос** теперь можно брать из `brainlink-com-macrotellect.jsonl` (официальный парсер на COM3).
- Признаки начала/конца кадра: префикс `aa aa`; для коротких и гиро — завершение `23 23`; для EEG `20 02` — кадр 50 байт с хвостом `23 23`.
- Контроль целостности (checksum/CRC): вероятно присутствует (по форме коротких кадров), пока не подтверждено.

## 3) Идентификаторы BLE (если BLE)

- Device Name pattern: `BrainLink_Pro`
- Service UUID: `6e400001-b5a3-f393-e0a9-e50e24dcca9e` (Nordic UART Service)
- Characteristic UUID (notify): `6e400003-b5a3-f393-e0a9-e50e24dcca9e` (Nordic UART TX, `notify`)
- Characteristic UUID (write, если требуется): `6e400002-b5a3-f393-e0a9-e50e24dcca9e` (Nordic UART RX, `write`/`write-without-response`)
- Нужна ли инициализирующая команда: поток подтверждён через `brainlink_stream_capture.py` (детали команды — по необходимости).

## 4) Карта данных EEG/гиро

- Attention:
- Meditation:
- Signal quality:
- Delta/Theta/Alpha/Beta/Gamma:
- Gyro X/Y/Z:
- Battery/temperature/heart rate (если есть):

## 5) Минимальный тест для MVP

1. Найти устройство в сканировании.
2. Подключиться и включить поток.
3. Получить минимум 30 секунд данных без разрыва.
4. Сохранить сырые пакеты в файл для отладки.
5. Подтвердить, что ключевые метрики стабильно парсятся.

Текущее состояние:
- [x] П.1 выполнен (найден `BrainLink_Pro`, MAC `C0:E2:FC:2D:AC:10`).
- [x] П.2 выполнен (BLE подключение и notify-подписка подтверждены).
- [x] П.3 выполнен (получен устойчивый поток, `docs/specs/brainlink-raw-capture.jsonl`).
- [x] П.4 выполнен (сырые данные сохранены в JSONL).
- [ ] П.5 в работе (декодер фреймов есть; осталось **подтвердить семантику полей** по эталону/доке).

## 6) Риски и вопросы

- Нужна ли авторизация/паринг перед чтением?
- Есть ли ограничения по версии BLE-адаптера?
- Отличается ли протокол между ревизиями Brainlink Pro?

## 7) Решение для первой версии

- Поддерживаемый канал:
- Поддерживаемый канал: BLE (NUS)
- Библиотека для подключения: `bleak`
- Формат внутреннего события `eeg.sample`: подготовить после выделения валидного EEG-фрейма из буфера notify.
- Что логируем в SQLite/CSV в MVP:

## 8) Быстрый запуск probe-скрипта

```bash
python scripts/brainlink_probe.py
python scripts/brainlink_probe.py --scan-time 20
python scripts/brainlink_probe.py --address "AA:BB:CC:DD:EE:FF"
```

- Результат сохраняется в `docs/specs/brainlink-ble-probe.json`.
- Если `bleak` не установлен: `pip install bleak`.

## 9) Захват сырого потока (следующий шаг)

```bash
python scripts/brainlink_stream_capture.py --address "C0:E2:FC:2D:AC:10"
python scripts/brainlink_stream_capture.py --address "C0:E2:FC:2D:AC:10" --duration 45
python scripts/brainlink_stream_capture.py --address "C0:E2:FC:2D:AC:10" --init-hex "aa2101"
```

- Выходной файл: `docs/specs/brainlink-raw-capture.jsonl`.
- Один пакет = одна строка JSON (`timestamp_utc`, `len`, `hex`).

## 10) Разделение буфера на фреймы и статистика

```bash
python scripts/brainlink_frame_splitter.py
python scripts/brainlink_frame_splitter.py --top 30
```

- Вход: `docs/specs/brainlink-raw-capture.jsonl`.
- Выход: `docs/specs/brainlink-frame-analysis.json`.
- Логика (черновая): разрезание по `aaaa ... 2323`, подсчёт длин фреймов и частых префиксов.

## 11) Декодирование (фиксированные длины)

```bash
python scripts/brainlink_frame_decoder.py
python scripts/brainlink_frame_decoder.py --max-samples 10
```

- Вход: `docs/specs/brainlink-raw-capture.jsonl`.
- Выход: `docs/specs/brainlink-frame-decode-report.json` (счётчики по типам, образцы, гипотезы checksum для коротких кадров).
- Важно: разрезание только по `2323` для длинных пакетов может давать ложные границы; для кода MVP использовать сканер как в `brainlink_frame_decoder.py` (EEG 50 байт, гиро 14 байт).

## 12) Сторонние репозитории (эталоны сверки)

Сводка ссылок Macrotellect, community BLE и смежных проектов — в отдельном файле **[brainlink-third-party-references.md](brainlink-third-party-references.md)** (стратегия сверки COM vs открытый BLE-исходник).

Разбор open-source парсера BaranovArtyom (модель кадра `AA AA` + длина + checksum): **[brainlink-barbanov-notes.md](brainlink-barbanov-notes.md)**.

## 13) Захват сырого потока с COM (без парсера)

Скрипт только пишет куски байт с порта в JSONL (как у BLE-capture: `timestamp_utc`, `len`, `hex`, поле `source: serial`).

```bash
pip install -e .
python scripts/brainlink_com_capture.py
python scripts/brainlink_com_capture.py --port COM3 --baud 115200 --duration 45
python scripts/brainlink_com_capture.py --port COM3 --duration 0
```

- Выход по умолчанию: `docs/specs/brainlink-com-raw-capture.jsonl`.
- Длительность `0` — писать до **Ctrl+C**.
- Дальше по смыслу: сравнить с `brainlink-raw-capture.jsonl` или прогнать `brainlink_frame_decoder.py --input ...` на COM-файле (формат строк тот же).

## 14) COM + официальный Macrotellect `BrainLinkParser.pyd`

1. Положить `BrainLinkParser.pyd` по инструкции: [`vendor/macrotellect_brainlink_parser/README.md`](vendor/macrotellect_brainlink_parser/README.md).
2. Запуск:

```bash
python scripts/brainlink_com_macrotellect.py
python scripts/brainlink_com_macrotellect.py --port COM3 --duration 60 --print-eeg
```

- Выход по умолчанию: `docs/specs/brainlink-com-macrotellect.jsonl` (события `eeg`, `extend`, `gyro`, `rr`, `raw`).
- Параметр `--pyd-dir` — если `.pyd` лежит не в `docs/specs/vendor/macrotellect_brainlink_parser/`.

## 15) Совместный захват COM + BLE (перекрывающийся UTC)

Для осмысленной сверки `brainlink_compare_macrotellect_ble.py` нужны **два файла одной физической сессии** (общее окно времени).

```bash
pip install -e .
python scripts/brainlink_concurrent_capture.py --address "C0:E2:FC:2D:AC:10" --port COM3 --duration 45 --stem docs/specs/brainlink-concurrent-session
```

- Пишет `<stem>-macrotellect.jsonl`, `<stem>-ble.jsonl`, `<stem>-session.json` (подсказка команд для compare).
- Далее: `python scripts/brainlink_compare_macrotellect_ble.py --macrotellect <stem>-macrotellect.jsonl --ble-raw <stem>-ble.jsonl` — ориентир на блок **`align_timestamp`** в отчёте (не `align_relative` / не индекс на разных сессиях).

**Критерий после сверки (рабочий порог, уточнять по данным):** для matched-пар по `align_timestamp` среднее абсолютное отклонение по полосам и attention/meditation — порядка единиц–десятков **или** зафиксировать в протокол-нотах, что расхождение ожидаемо (разные каналы / фильтры), если эталон и BLE расходятся системно.

## 16) Оркестратор «одна сессия» (скрипт)

Из корня репозитория:

```bash
# Только сводка по уже готовому отчёту compare
python scripts/brainlink_verify_one_session.py --summary

# Только compare + сводка (если уже есть <stem>-macrotellect.jsonl и <stem>-ble.jsonl)
python scripts/brainlink_verify_one_session.py --compare --stem docs/specs/brainlink-concurrent-session

# Полный цикл: совместный захват + compare + сводка (нужны гарнитура, COM, BLE MAC)
python scripts/brainlink_verify_one_session.py --all --address "C0:E2:FC:2D:AC:10" --stem docs/specs/brainlink-concurrent-session
```

Скрипт сам выполняет `chdir` в корень репозитория. В сводке в консоли выводятся **`align_timestamp.matched_pairs`**, медиана `abs_delta_t`, топ MAE по полям и первая пара; полный JSON — в `--report` (по умолчанию `docs/specs/brainlink-macrotellect-vs-ble.json`).

**Зафиксированные пороги MAE (заполнить после первого валидного прогона):** _пока не заданы — после сессии с `matched_pairs > 0` вписать сюда числа по полям или ссылку на строку в отчёте._

