# Техническое задание — Telegram AI AntiSpam (итерационная разработка AI-агентом)

## 1. Цель
Self-hosted бот для защиты Telegram групп/каналов от спама на множестве языков. Установка в один клик на Ubuntu 22/24, настройка через терминал, обновления без потери данных. Продакшн-альтернатива — https://tg.vega.chat.

## 2. Стек
Python 3.10+, aiogram 3.x, aiosqlite, openai (для api.vega.chat OpenAI-совм.), pydantic, PyYAML, systemd. Без Docker (опционально позже).

## 3. Роли и сценарии

### 3.1 Установка (one-click)
1. Пользователь запускает `curl ... | bash` или `bash install.sh`.
2. Скрипт печатает инструкцию:
   - `@BotFather -> /newbot -> name_bot -> токен`
   - `api.vega.chat -> регистрация -> Dashboard -> API key`
3. Запрос `Продолжить Y / Стоп N`. При N — выход.
4. При Y — последовательно спрашивает (Enter = пропуск/default):
   - `BOT_TOKEN`
   - `ALLOWED_CHATS` (ID `-100...` или `@username`, через запятую; пусто = все где бот админ)
   - `WHITELIST_USERS`
   - `DEFAULT_LANGUAGE` (ru/en/tr/uk/kz...)
   - `VEGA_API_KEY`
   - `VEGA_MODEL` (список с api.vega.chat/models, default z-ai/glm-5.3-flash)
5. Создает `/opt/tg-antispam`, `.venv`, `pip install -e .`, `.env` + `config/config.yaml` (если уже есть — не перетирать), `data/bot.db`, `systemd` сервис `tg-antispam`.
6. Печатает финальную инструкцию: добавить бота в группу/канал админом + ссылка на tg.vega.chat + команды CLI + логи.

Критерии: идемпотентность, повторный запуск не ломает конфиг/БД.

### 3.2 Эксплуатация
- Бот должен быть админом (delete + ban). Иначе — лог warning.
- `sudo journalctl -u tg-antispam -f`, `systemctl restart/status`.
- CLI: `python -m src.cli.manage status|add-chat|add-whitelist|set-prompt|set-model` — меняет `config/config.yaml`, требует рестарт.
- `filter_prompt.txt` редактируется вручную.

### 3.3 Обновление
`bash update.sh`: `git pull`, `pip install -e .`, `daemon-reload + restart`. НЕ трогает `.env`, `config/config.yaml`, `data/bot.db`.

## 4. Функциональные требования

### 4.1 Испытательный срок (probation) вместо капчи
- Капча-ловушка УДАЛЕНА (ловила только LLM-ботов и давала фрикцион людям). Замена — тихий probation.
- Триггер: `chat_member` + `new_chat_members` — `record_join` идемпотентен (INSERT OR IGNORE), двойная отправка не возникает.
- Юзер «новый» пока `joined_ts < PROBATION_HOURS` (24ч) ИЛИ `msg_count < PROBATION_MSGS` (5).
- Строгий режим: любая реальная ссылка (видимая, `text_link`-entity, url-кнопка) от нового юзера → спам без LLM. @mention не считается.
- Скан профиля при входе (`BIO_SCAN`): `getChat(user_id)` → bio+имя+username; ссылка+запрещённая тема → перманентный мьют; только ссылка → `flagged` (продленный probation).
- Все медиа новых юзеров и форвардов → vision (`VISION_MODE=always|suspect|new_users|off`).

### 4.1.1 Наказания
- `MUTE_POLICY=permanent` (default): первое нарушение → перманентный мьют. `progressive` — эскалация 1д/7д/пермач. `BAN_ON_REPEAT_SPAM` — бан вместо мьюта.
- Админы чата и whitelist не наказываются.

### 4.2 Антиспам фильтрация
- Триггер: каждое сообщение (text/caption) + медиа с подписью.
- Порядок:
  1. Сохранить в `recent_messages` (3 последних/чат, для восстановления после падения).
  2. Private-чаты — skip сразу. Whitelist/admin чата — skip.
  3. Если `allowed_chats` задан и чат не в списке — skip.
  4. Rate-limit: >RATE_LIMIT_COUNT сообщений за RATE_WINDOW_SEC → flood без LLM.
  5. Скрытые ссылки (`text_link` entities + url-кнопки) добавляются в анализ как `[LINK]`.
  6. Probation: новый юзер + реальная ссылка → spam без LLM.
  7. Кэши: `verdicts` по хэшу текста, `media_verdicts` по `file_unique_id` — повторы без LLM.
  8. Эвристика: `link + casino/sex/drugs` → сразу spam (без LLM).
  9. Иначе → `ai_is_spam(text, image_url, context)` через `api.vega.chat/v1/chat/completions` с `filter_prompt.txt`. Ответ JSON `{spam,reason,category}`. При ошибке LLM — strict-fallback (ссылка/форвард+тема → удалять), чистого fail-open нет.
  10. Если spam и `DELETE_SPAM` и групповой чат → `delete()` + наказание по `MUTE_POLICY`.
- Edited-сообщения (`edited_message`) проверяются тем же пайплайном.
- Категории: `link, sex, drugs, casino, crypto, games, illegal, ads, other`.
- Картинки/стикеры/превью: `image_url` передаётся vision-модели (`VEGA_VISION_MODEL`, пусто = основная). Кэш по `file_unique_id` — дедуп репостов.

### 4.3 Persistence
- SQLite `data/bot.db`: `recent_messages`, `member_state`, `verdicts`, `media_verdicts`, `violations`, `mute_state`, `kv`. Инициализация при старте. 3 последних сообщения/чат.
- После рестарта recent_messages доступны, пропуск рекламы не происходит (сообщения после падения — это новые апдейты polling/getUpdates, но контекст есть).

### 4.5 Личность и чат-режим
- У бота есть разговорная личность (промпт `config/chat_prompt.txt`). Модерация идёт первой — спам не получает ответа.
- Триггеры в группах: @упоминание бота, reply на её сообщение, имя в тексте (`дейнерис`, `дени`, `daenerys`, `dany`, `кхалиси`, `вега`, `vega`).
- Свобода воли: модель сама решает — ответить или вернуть `[SILENT]`/`[IGNORE]` → сообщение не отправляется.
- `CHAT_AMBIENT_PCT` — % чистых сообщений без триггера, которые личность видит и решает сама (каждое = 1 LLM-вызов).
- Админы бота/whitelist/админы чата пропускают модерацию, но получают чат-ответы.
- ЛС: отвечает только `ADMIN_USER_IDS` (создатели). Чужие получают вежливый отказ без LLM-вызова (троттлинг 2/5мин).
- `VEGA_CHAT_MODEL` — отдельная модель для чата; `CHAT_WEB_SEARCH=true` добавляет `:online` (OpenRouter web-search). `CHAT_MAX_TOKENS` — бюджет reasoning+ответ+image; короткость ответа задаёт промпт, не лимит. Чат-вызовы просят `reasoning.effort=low` (фолбэк без параметра).

### 4.4 Мультиязык
- `DEFAULT_LANGUAGE` → язык уведомлений, промпт AI мультиязычный.

## 5. Нефункциональные
- Ubuntu 22/24, systemd `Restart=always`.
- Логи INFO, без секретов.
- Валидация: токен `^\d+:[\w-]+$`, VEGA key не пустой, чаты `^-100\d+|@\w+$`.
- Безопасность: `.env` 600, токены не логировать.

## 6. Конфигурация
`.env` + `config/config.yaml` (yaml приоритетнее), `config/filter_prompt.txt`, `data/bot.db`. `config.example.yaml`, `.env.example` в репо.

## 7. Итерации для AI-агента
1. Скелет + install/update/cli/db — done
2. Капча + эвристики
3. Vega AI фильтр + промпт
4. Медиа/vision + тесты
5. Админ-команды в ТГ + метрики
6. Docker/CI (опц.)

## 8. Приемка
- [ ] `bash install.sh` с Y/N, вопросами, валидацией, идемпотентностью
- [ ] Бот стартует, `systemctl status` ok
- [ ] Probation: ссылка от новичка удаляется без LLM; bio с ссылкой+запреткой → мьют
- [ ] Спам с ссылкой/казино/секс удаляется, белый список не трогается
- [ ] `filter_prompt.txt` редактируется и влияет
- [ ] После `kill -9` + рестарт `recent_messages` на месте
- [ ] `update.sh` не теряет настройки
- [ ] Инструкция про админа и tg.vega.chat выводится

## 9. Вне скоупа v1
Биллинг, веб-дашборд, шардирование, анти-рейд, OCR стикеров.
