# Agent runtime bootstrap (cloud/local/heuristic)

Цель: получить автономный контур принятия решений до/после исчерпания cloud-лимитов.

## Что добавлено

- В PoC «Программатор» справа: блок **«Чат с моделью (Ollama)»** — «Свободное общение», режим **промпта** (JSON: `set_spec` / `set_timeline` / `stop` / `hold`), опционально **«Автоприменение JSON»**, кнопка **«Применить команду»**, строка **«Шаблоны»** (Стоп / Шум 30с / Тета / Тишина); настройки чата **сохраняются в JSON-профиль** при закрытии окна (`NSP_PROFILE_PATH` или `~/.neurosync_pro/ui_profile.json`).

- `tools/agent_runtime.py` — runtime в 3 режимах:
  - `cloud`: OpenAI-compatible `/chat/completions`
  - `local`: Ollama `/api/chat`
  - `heuristic`: полностью локальный fallback
- `tools/evaluate_policy.py` — оффлайн оценка на JSONL сессии
- `src/neurosync_pro/agent_runtime/*` — контракт решения, провайдеры, fallback и cooldown

## Контракт решения

Модель (или fallback) должна вернуть JSON.

Примеры:

```json
{
  "action": "set_spec",
  "spec": "200+10/0.55 pink/0.06",
  "confidence": 0.72,
  "reason_code": "model_decision"
}
```

```json
{
  "action": "set_timeline",
  "timeline": "0:00 white/0.70\n0:30 off",
  "confidence": 0.9,
  "reason_code": "timer"
}
```

```json
{ "action": "stop", "confidence": 1.0, "reason_code": "user" }
```

Допустимые `action`: `set_spec`, `set_timeline`, `hold`, `stop`.

При любой ошибке парсинга/валидации runtime автоматически переводит решение в безопасный `hold`.

## Живой хвост сессии (`--follow`)

Пока UI пишет JSONL, можно не перезапускать скрипт после каждой новой строки:

```bash
python tools/agent_runtime.py --mode heuristic --follow -v
```

- По умолчанию обрабатываются **только новые** строки `observation` (файл открывается с конца).
- `--replay` — при первом открытии файла прочитать его с начала (можно долго и дорого для LLM).
- Интервал опроса: `NSP_TAIL_POLL_S` (по умолчанию `0.5`).
- Фиксированный файл: `--session-file path/to/session.jsonl --follow`.

## Быстрый старт

1) Поднимите UI (`neurosync-pro meditation`), включите:
- `Agent API :8765`
- запись сессии

2) Запустите runtime (локальная модель):

```bash
python tools/agent_runtime.py --mode local --dry-run
```

3) Боевой запуск:

```bash
python tools/agent_runtime.py --mode local
```

## Переменные окружения

Общие:
- `NSP_SESSION_DIR` (по умолчанию `docs/specs/sessions`)
- `NSP_UI_AGENT_API_URL` (по умолчанию `http://127.0.0.1:8765/v1/event`)
- `NSP_COOLDOWN_S` (по умолчанию `12`)
- `NSP_LLM_DEBOUNCE_MATCHES` (по умолчанию `2`) — для режимов `local`/`cloud`: сколько **подряд одинаковых** `set_spec` / `set_timeline` по окнам `observation` нужно, прежде чем команда пройдёт (поверх cooldown). Значение `1` — выключить debounce.
- `NSP_LLM_DEBOUNCE_CONF_BYPASS` (не задано по умолчанию) — если задан числом в `[0, 1]`, при **`confidence` ≥ этого порога** для данного решения эффективное число совпадений считается **`1`** (debounce для этого шага фактически не мешает).
- `NSP_LLM_DEBOUNCE_CONF_STRICT_LT` (не задано по умолчанию) — если задан числом в `[0, 1]`, при **`confidence` ниже этого порога** к базовому числу повторов добавляется **`+1`** (не выше внутреннего потолка `10`).
- `NSP_LLM_RATE_LIMIT_PER_MIN` (по умолчанию `0`) — не более стольких **успешных** смен программы (`set_spec` и `set_timeline` в сумме) за скользящее окно; `0` — выключено. Учитываются только реальные коммиты после debounce и cooldown.
- `NSP_LLM_RATE_LIMIT_WINDOW_S` (по умолчанию `60`) — длина окна в секундах для лимита выше (минимум `1`).
- `NSP_CHAT_METRIC_LOOP_S` (по умолчанию `30`) — период в секундах для кнопок **Attention / Meditation / A/M** в чате PoC (автотакт метрик; минимум `5`, максимум `600`).

Local mode (Ollama):
- `NSP_LOCAL_BASE_URL` (по умолчанию `http://127.0.0.1:11434`)
- `NSP_LOCAL_MODEL` (по умолчанию `next2-local`)
- `NSP_MODEL_TIMEOUT_S` (по умолчанию `15`)

CLI: можно передать имя модели без env:  
`python tools/agent_runtime.py --mode local --local-model deepseek-v3.1:671b-cloud --dry-run`

### Ollama Cloud (модели `*-cloud`)

В `api/tags` такие модели помечены как `remote_host` (прокси через `ollama.com`). Запрос всё равно идёт на **локальный** `POST /api/chat`; Ollama сам ходит в облако.

- Нужны **интернет** и обычно **вход в аккаунт Ollama** (`ollama signin`) и подписка/квота — иначе запрос может упасть по авторизации.
- Первый ответ может идти **долго**; для теста поднимите таймаут, например:  
  `$env:NSP_MODEL_TIMEOUT_S="120"` (PowerShell).
- Для проверки без нашего runtime:

```powershell
curl.exe -s http://localhost:11434/api/chat -H "Content-Type: application/json" -d "{\"model\":\"deepseek-v3.1:671b-cloud\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with JSON only: {\\\"action\\\":\\\"hold\\\"}\"}],\"stream\":false}"
```

Подключение к пульту: `--mode local` + `--local-model deepseek-v3.1:671b-cloud` — это и есть «cloud-модель через Ollama» (режим `--mode cloud` в скрипте — это **другой** провайдер: OpenAI-compatible API, не Ollama).

Cloud mode:
- `NSP_CLOUD_BASE_URL` (по умолчанию `https://api.openai.com/v1`)
- `NSP_CLOUD_MODEL` (по умолчанию `gpt-4o-mini`)
- `NSP_CLOUD_API_KEY` (обязателен для `--mode cloud`)
- `NSP_MODEL_TIMEOUT_S` (по умолчанию `20`)

## Оценка политики на логе

```bash
python tools/evaluate_policy.py src/docs/specs/sessions/meditation_20260426_063208.jsonl --mode heuristic
```

Метрики:
- `hold_rate`
- `switch_rate`
- latency (`mean`, `p95`)

## Рекомендованный rollout

1. `--mode heuristic` (база + проверка контура)
2. `--mode local` (tiny-model)
3. `--mode cloud` как quality-бустер/архитектор
4. держать fallback активным всегда

## Поэтапный план развития (кратко)

**Сделано:** этап 1 — чат в режиме промпта (`set_spec` / `set_timeline` / `stop`), автоприменение; этап 2 — **`agent_runtime --follow`** (хвост JSONL); шаблонные кнопки под полем чата; этап 3 — **строгая проверка грамматики** `spec` и строк timeline (`spec_validate.py`) в контракте решения и в UI перед запуском программатора; этап 4 — **профиль пользователя JSON** (`user_profile.py`): URL/модель Ollama, галочки режима чата; сохранение при закрытии PoC, загрузка при старте. Путь: `~/.neurosync_pro/ui_profile.json` или переменная **`NSP_PROFILE_PATH`**.

**Анти-дёргание LLM:** `NSP_LLM_DEBOUNCE_MATCHES` + существующий `NSP_COOLDOWN_S` + лимит частоты **`NSP_LLM_RATE_LIMIT_PER_MIN`** / **`NSP_LLM_RATE_LIMIT_WINDOW_S`** в `step_observation` (`loop.py`): порядок **`decide` → debounce → cooldown → rate_limit → commit**; при превышении лимита — `hold` с `reason_code` **`rate_limit`**.

**Учёт confidence в debounce:** опционально **`NSP_LLM_DEBOUNCE_CONF_BYPASS`** и **`NSP_LLM_DEBOUNCE_CONF_STRICT_LT`** (см. выше).

**Чат PoC (программатор):** по умолчанию JSON применяется сразу после валидации (ощущение раннего этапа). Чекбокс **«Ограничения как у agent_runtime»** и ключ профиля **`chat_agent_runtime_policy`** включают **`apply_decision_policy`** (`local`, те же `NSP_*`). При ответе модели с **`hold`** streak debounce сбрасывается.
