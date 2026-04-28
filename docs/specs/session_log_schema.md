# Session log schema (JSONL) — дневник для обучения (A)

Файл сессии — это **JSONL**: одна строка = один JSON-объект.

## Общие поля (почти везде)

- `type`: тип события
- `session_id`: UUID сессии
- `timestamp_utc`: ISO-строка времени
- `t_monotonic_s`: секунды от старта сессии (монотонные)
- `source`: `ble|jsonl|ui`

## События

### 1) `session_start`

Пишется при открытии файла лога / старте BLE.

Поля:
- `app.window`
- `device.ble_address`
- `programmer.spec` (последняя/стартовая)

### 2) `session_end`

Пишется при закрытии окна.

Поля:
- `summary.duration_s`

### 3) `eeg`

Пишется на каждом обновлении метрик.

Поля:
- `eeg.attention`
- `eeg.meditation`
- `quality.sq` (последнее)
- `quality.rssi` (последнее)

### 4) `bands`

Пишется при приходе bands.

Поля:
- `bands` (словарь полос)

### 5) `hr`

Пишется при приходе HR.

Поля:
- `hr.bpm`
- `hr.source`

### 6) `program.action`

Пишется при управлении программатором.

Поля:
- `action.command`: `set_spec|set_timeline|stop`
- `action.by`: `ui|agent|timeline`
- `action.spec` (для `set_spec`)
- `action.timeline` (для `set_timeline`)

### 7) `program.status`

Сейчас публикуется в `EventBus` и может уходить в Sink URL. В лог пишется в составе `observation.program`.

### 8) `observation`

Пишется примерно раз в `window_s` секунд (по умолчанию 10s) и содержит агрегаты по окну.

Поля:
- `window_s`
- `eeg.attention`: `{mean,min,max,std}` или `null`
- `eeg.meditation`: `{mean,min,max,std}` или `null`
- `hr`: `{mean,min,max,std}` или `null`
- `quality.sq_last` (последнее)
- `quality.sq`: `{mean,min,max,std}` (по окну) или `null`
- `quality.rssi`: `{mean,min,max,std}` (по окну) или `null`
- `bands_last` (последний словарь полос за окно)
- `program` (что было активно на момент записи observation)

### 9) `marker`

Ручные заметки (дневник). Добавляются кнопкой `Marker Add` в правой панели.

Поля:
- `marker.label` (строка)
- `marker.rating` (целое -2..+2)
- `marker.note` (строка или null)

## Пример observation

```json
{
  "type":"observation",
  "session_id":"...",
  "timestamp_utc":"2026-04-26T05:00:00Z",
  "t_monotonic_s":120.0,
  "window_s":10,
  "eeg":{"attention":{"mean":55,"min":48,"max":61,"std":3.2},"meditation":{"mean":42,"min":35,"max":50,"std":4.1}},
  "hr":{"mean":78,"min":76,"max":80,"std":1.4},
  "quality":{"sq_last":0,"sq":{"mean":0,"min":0,"max":0,"std":0},"rssi":{"mean":-78,"min":-82,"max":-75,"std":2.1}},
  "bands_last":{"delta":...},
  "program":{"running":true,"spec":"100+7/0.60 pink/0.08","tone":{"l_hz":96.5,"r_hz":103.5,"vol":0.6},"noise":{"color":"pink","vol":0.08}}
}
```

