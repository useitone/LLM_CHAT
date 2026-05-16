# Свет iPIXEL (матрица) в NeuroSync Pro

Кратко: метрики **Attention / Meditation** с шины `eeg.metrics` превращаются в **`light.intent`** (RGB), затем фоновый BLE‑воркер шлёт на панель **PNG‑заливку** в формате, совместимом с библиотекой [pypixelcolor](https://github.com/lucagoc/pypixelcolor) и описанием [iPIXEL Protocol](https://github.com/cagcoach/ha-ipixel-color/blob/main/iPIXEL-Protocol-Documentation.md).

## Два способа настройки

1. **Окно PoC «Свет (iPIXEL)»** — галочки, MAC, размер матрицы, dry-run, fade (мс, max steps, min step, уважать MIN_INTERVAL), пульс по яркости, повторы кадра при ошибке BLE, **лог повторов кадра** (`NSP_LIGHT_BLE_RETRY_DEBUG`), поля **файла правил AUTO** и **лога intents**, пресеты ручного RGB, кнопка **«Применить настройки света»** (пишет в `os.environ` и переподключает мосты без перезапуска).
2. **Переменные окружения** `NSP_LIGHT_*` в PowerShell до запуска `neurosync-pro meditation` — удобно для скриптов.

Профиль UI (`~/.neurosync_pro/ui_profile.json`, ключ `light_ui`) сохраняется при **закрытии** окна медитации вместе с настройками чата.

**Проверка на железе:** пошаговый чеклист — [light-hardware-checklist.md](light-hardware-checklist.md).

```
ЭЭГ (BLE / JSONL / API) → eeg.metrics → MetricsLightBridge → light.intent
                                                      ↓
                              LightIntentSink + BleSolidRgbWorker → BLE GATT
```

## EEG → Tone (Mono) и свет

В **Mono**, в **Vol source** есть режимы **«Volume + Light»**, привязанные к одной метрике:

- **Meditation → Volume + Light** — громкость тона линейно от **Meditation**; цвет как в авто, но **только по M**: при **M ≥ NSP_LIGHT_AUTO_MED_THRESHOLD** — «выше порога», иначе «ниже» (`rgb_meditation_volume_light`). Цвета RGB: строки `r,g,b` в переменных **`NSP_LIGHT_VOL_LIGHT_MED_ABOVE_RGB`** / **`NSP_LIGHT_VOL_LIGHT_MED_BELOW_RGB`** (по умолчанию как прежние calm_blue / idle). В PoC: блок **«Цвета Volume+Light (Mono)»** в настройках EEG→Tone; в окружение при **«Применить настройки света»**.
- **Attention → Volume + Light** — громкость от **Attention**; цвет **только по A** и порогу **NSP_LIGHT_AUTO_ATT_THRESHOLD** — **`NSP_LIGHT_VOL_LIGHT_ATT_ABOVE_RGB`** / **`NSP_LIGHT_VOL_LIGHT_ATT_BELOW_RGB`** (по умолчанию focus_warm / idle). Те же спинбоксы в PoC.

На каждом тике тона публикуется `light.intent` с `source: eeg_tone_volume_light`. Чтобы цвет не перезаписывался вторым `light.intent` от моста **ЭЭГ → цвет** в том же кадре, при активном режиме Volume+Light на время публикации `eeg.metrics` выставляется внутренняя переменная **`NSP_LIGHT_SKIP_AUTO_LIGHT`** (ручную установку в окружении для этого обычно не нужно).

В режиме **Stereo** эти пункты **Vol source** не используются (отдельные источники громкости L/R).

## EEG → Binaural и пульс матрицы (эксперимент)

При включённом **EEG → Binaural** и галочке **«Матрица: пульс от несущей»** в шину уходит чередующийся `light.intent` (`source: eeg_bin_matrix_pulse`) между двумя RGB (**`NSP_LIGHT_BIN_PULSE_RGB_A`** и **`NSP_LIGHT_BIN_PULSE_RGB_B`**, по умолчанию как прежние calm_blue / idle). Частота полного цикла мерцания — **линейная** от сглаженной несущей `_eeg_bin_base_hz` в диапазоне **Base Hz min–max** (как у бинаурала) в диапазон **«Пульс Гц min–max»** в UI. Реальный темп ограничен **`NSP_LIGHT_BLE_MIN_INTERVAL_S`** (каждая смена кадра не чаще одного интервала на половину шага). В PoC цвета фаз задаются в блоке **«Цвета пульса матрицы»**; в окружение — при **«Применить настройки света»**.

На кадре `eeg.metrics` авто-`light.intent` с моста метрик **подавляется** (как при Volume+Light), чтобы не перебивать пульс.

**Предупреждение для других пользователей:** мерцание может быть интенсивным и настраивается под эксперимент; при чувствительности к мерцающему свету режим не использовать.

## Ручной RGB (MANUAL в PoC)

В блоке **«Свет (iPIXEL)»**: спинбоксы **R / G / B** и кнопка **«Применить ручной RGB»** публикуют `light.intent` с `source: light_manual` (тот же подписчик BLE/log). Галочка **«Удерживать ручной RGB»** подавляет авто-`light.intent` с моста метрик на каждом `eeg.metrics` (чтобы ручной кадр не перезаписывался), аналогично Volume+Light / пульсу бинаурала.

## Переменные окружения

| Переменная | По умолчанию | Назначение |
|------------|--------------|------------|
| `NSP_LIGHT_ENABLED` | `0` | `1` — считать цвет из метрик (`NSP_LIGHT_MODE`). |
| `NSP_LIGHT_MODE` | `log` | `auto` — пороги → RGB; `log` — без `light.intent` (только stderr при `NSP_LIGHT_DEBUG`). |
| `NSP_LIGHT_AUTO_MED_THRESHOLD` | `70` | Порог Meditation (0…100) для calm_blue в авто и в **Meditation → Volume + Light** (свет). |
| `NSP_LIGHT_AUTO_ATT_THRESHOLD` | `70` | Порог Attention для focus_warm в авто и в **Attention → Volume + Light** (свет). |
| `NSP_LIGHT_VOL_LIGHT_MED_ABOVE_RGB` | `100,150,255` | CSV `r,g,b` для M+Light при M ≥ порога (Volume+Light Mono). |
| `NSP_LIGHT_VOL_LIGHT_MED_BELOW_RGB` | `24,28,36` | CSV для M+Light при M < порога. |
| `NSP_LIGHT_VOL_LIGHT_ATT_ABOVE_RGB` | `255,255,200` | CSV для A+Light при A ≥ порога. |
| `NSP_LIGHT_VOL_LIGHT_ATT_BELOW_RGB` | `24,28,36` | CSV для A+Light при A < порога. |
| `NSP_LIGHT_BIN_PULSE_RGB_A` | `100,150,255` | Первая фаза пульса матрицы (Binaural). |
| `NSP_LIGHT_BIN_PULSE_RGB_B` | `24,28,36` | Вторая фаза пульса матрицы. |
| `NSP_LIGHT_AUTO_RULES_PATH` | — | Путь к JSON с порядковыми правилами AUTO (если задан и файл читается — **им** задаётся цвет; пороги `NSP_LIGHT_AUTO_*_THRESHOLD` для авто не используются). Формат: объект `{"idle":[r,g,b],"rules":[...]}` или массив `rules`; правило: `metric` (`meditation` \| `attention`), `op` (`>=` \| `>`), `value` (0…100), `rgb`. Первое совпадение по порядку; иначе `idle`. Пример: [`docs/light-auto-rules.example.json`](light-auto-rules.example.json). Кэш по mtime; сброс при **«Применить настройки света»** в UI. |
| `NSP_LIGHT_INTENT_LOG` | — | Путь к файлу: после дедупа по RGB+яркости дописывается **одна JSON-строка на событие** (`t_unix`, `rgb`, `source`, `attention`, `meditation`). Удобно для отладки и сбора данных без отдельного сервиса. |
| `NSP_LIGHT_SEND_ENABLED` | `0` | `1` — подписчик на `light.intent`. |
| `NSP_LIGHT_SEND_MODE` | `log` | `ble` — отправка на матрицу; `log` — только лог при `NSP_LIGHT_SEND_DEBUG=1`. |
| `NSP_LIGHT_SEND_DEBUG` | `0` | Для режима log: строки `[light][send]` в stderr. |
| `NSP_LIGHT_BLE_PROTOCOL` | `raw` | **`ipixel_png`** — PNG заливка + окна как у pypixelcolor; **`raw`** — `NSP_LIGHT_BLE_RGB_PREFIX_HEX` + 3 байта RGB. |
| `NSP_LIGHT_BLE_ADDRESS` | — | MAC панели (обязательно для реальной отправки). |
| `NSP_LIGHT_BLE_WRITE_UUID` | (для `ipixel_png`) `0000fa02-0000-1000-8000-00805f9b34fb` | GATT write. |
| `NSP_LIGHT_BLE_NOTIFY_UUID` | `0000fa03-0000-1000-8000-00805f9b34fb` | Notify для ACK (см. ниже). |
| `NSP_LIGHT_BLE_MATRIX_W` / `H` | `96` / `16` | Размер PNG под вашу матрицу (например 32×16). |
| `NSP_LIGHT_BLE_DRY_RUN` | `1` | **`1`** — не писать в радио, только лог hex в stderr. **`0`** — реальный BLE. |
| `NSP_LIGHT_BLE_IPX_INIT` | `1` | После connect: команды включения и яркости (`NSP_LIGHT_BLE_IPX_BRIGHTNESS`, 1–100). |
| `NSP_LIGHT_BLE_IPX_WAIT_ACK` | `1` | Подписка на notify и ожидание ACK после каждого **окна** передачи (надёжность, как в pypixelcolor). `0` — старый режим без ожидания. |
| `NSP_LIGHT_BLE_WRITE_CHUNK_RESPONSE` | `1` | `write_gatt_char(..., response=True)` для чанков (как в pypixelcolor). `0` — write without response (быстрее, рискованнее). |
| `NSP_LIGHT_BLE_ACK_TIMEOUT_S` | `8` | Таймаут ожидания ACK на одно окно (и верхняя граница для init). |
| `NSP_LIGHT_BLE_MIN_INTERVAL_S` | `0.05` | Минимальный интервал между **очередными** сменами RGB из `light.intent` (троттлинг перед началом обработки одного логического цвета). Значение читается воркером **при старте** потока BLE; после смены в UI нажмите **«Применить настройки света»**. |
| `NSP_LIGHT_BLE_FADE_MS` | `0` | Длительность плавного перехода **линейным RGB** от предыдущего целевого цвета к новому (мс). `0` — без fade. Каждый шаг — полный кадр (`ipixel_png` или `raw`). **Важно:** между шагами fade **не** действует `MIN_INTERVAL` — только пауза из `fade_ms`/числа шагов (`post_sleep`) и опционально `FADE_MIN_STEP_S` / `FADE_RESPECT_MIN_INTERVAL`. При больших W×H и длинном fade возможен **burst** полных PNG подряд; см. [light-hardware-checklist.md](light-hardware-checklist.md). |
| `NSP_LIGHT_BLE_FADE_MAX_STEPS` | `32` | Верхняя граница числа шагов fade (2…64). Уменьшайте на больших матрицах, чтобы снизить число полных кадров за один переход. |
| `NSP_LIGHT_BLE_FADE_MIN_STEP_S` | `0` | Минимальная пауза (с) **между шагами** fade после каждого полного кадра; `0` — только распределение `fade_ms` по шагам (`post_sleep`). Ненулевое значение (например `0.05`) смягчает burst. |
| `NSP_LIGHT_BLE_FADE_RESPECT_MIN_INTERVAL` | `0` | Если `1` — между шагами fade дополнительно не меньше `NSP_LIGHT_BLE_MIN_INTERVAL_S` (вместе с `FADE_MIN_STEP_S` берётся максимум). |
| `NSP_LIGHT_BLE_PULSE_HZ` | `0` | После доставки цвета — пульсация **яркости** (множитель на RGB) с заданной частотой (Гц), пока в очередь не попадёт новый RGB. `0` — выключено. |
| `NSP_LIGHT_BLE_FRAME_RETRIES` | `2` | Сколько раз подряд повторять **полный кадр** при ошибке (таймаут ACK, обрыв, запись). |
| `NSP_LIGHT_BLE_FRAME_RETRY_DELAY_S` | `0.15` | Пауза (с) перед следующей попыткой полного кадра. |
| `NSP_LIGHT_BLE_RETRY_DEBUG` | `0` | Если `1` — в stderr строки о неудачных попытках кадра и об успехе после повтора (`ipixel_png` и `raw`). |
| `NSP_LIGHT_BLE_WRITE_CHUNK` | `244` | Размер чанка GATT (макс. 244). |
| `NSP_LIGHT_BLE_CONNECT_TIMEOUT` | `8` | Таймаут подключения bleak. |
| `NSP_LIGHT_BLE_CONNECT_RETRIES` | `3` | Повторные попытки `connect` при ошибках вроде «device not found» (ipixel_png и raw). |
| `NSP_LIGHT_BLE_CONNECT_RETRY_DELAY_S` | `0.4` | Пауза между попытками подключения (с). `0` — без паузы. |
| `NSP_LIGHT_BLE_IPX_SAVE_SLOT` | `0` | Слот сохранения в прошивке (0 — «живой» буфер в типичных сценариях). |
| `NSP_LIGHT_BLE_RGB_PREFIX_HEX` | — | Только для `raw`: hex префикс перед RGB. |

## Надёжность (ACK)

Для `ipixel_png` и `NSP_LIGHT_BLE_DRY_RUN=0` воркер:

1. Подключается к панели.
2. Включает **notify** на `fa03` (или `NSP_LIGHT_BLE_NOTIFY_UUID`).
3. После каждого полного **окна** PNG (все чанки до 244 байта) ждёт notify с кодом **0, 1** или **3** (логика как в `pypixelcolor` `AckManager`).
4. После передачи кадра вызывает `stop_notify`, чтобы следующая смена цвета снова подняла подписку.

Если `start_notify` не удался (драйвер / права), в stderr будет предупреждение и передача пойдёт **без ожидания ACK** (короткая пауза между окнами).

При таймауте ACK смотрите строку `[light][ble] ipixel_png failed: ...` — увеличьте `NSP_LIGHT_BLE_ACK_TIMEOUT_S` или временно отключите ожидание: `NSP_LIGHT_BLE_IPX_WAIT_ACK=0`.

## Типичные проблемы

| Симптом | Что проверить |
|---------|----------------|
| В консоли dry-run, матрица молчит | `NSP_LIGHT_BLE_DRY_RUN=0`, кнопка «Применить» в UI. |
| Нет реакции при `DRY_RUN=0` | MAC, размер матрицы W×H, не занята ли панель только телефоном. |
| Обрывы / зависания | ACK: уменьшить частоту смены цвета (`NSP_LIGHT_BLE_MIN_INTERVAL_S`), включить ожидание ACK (`IPX_WAIT_ACK=1`). |
| Тормоза / таймауты при **fade** | Уменьшить `NSP_LIGHT_BLE_FADE_MS` или `FADE_MAX_STEPS`; включить `FADE_RESPECT_MIN_INTERVAL=1` и/или `FADE_MIN_STEP_S`; увеличить `ACK_TIMEOUT`. Чеклист: [light-hardware-checklist.md](light-hardware-checklist.md). |
| Два BLE на одном адаптере нестабильны | Тест матрицы без `--ble-address` + Agent API и ручные `eeg.metrics`. |

## Запуск

```powershell
pip install -e ".[gui]"
neurosync-pro meditation --ble-address "MAC_ОБОДА"
```

Или без обода: включить **Agent API** в окне и слать `POST http://127.0.0.1:8765/v1/event` с телом `{"topic":"eeg.metrics","payload":{"attention":30,"meditation":80}}`.

## Источники протокола

- [iPIXEL-Protocol-Documentation.md](https://github.com/cagcoach/ha-ipixel-color/blob/main/iPIXEL-Protocol-Documentation.md) — UUID `fa02` / `fa03`, структура команд.
- [lucagoc/pypixelcolor](https://github.com/lucagoc/pypixelcolor) — референс по ACK и chunking (MIT).
