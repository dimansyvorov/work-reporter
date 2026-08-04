# Спринтовый отчёт (GitLab + Jira)

Локальный веб-отчёт по текущему спринту: прогресс направлений, эпики, релизы, списания времени, риски и рейтинги сотрудников.

Данные собираются из Jira (задачи, спринты, worklog, версии) и GitLab (MR/коммиты), считаются локально и открываются в браузере на `localhost`.

## Возможности

- Сводка спринта и прогресс по направлениям команды
- Таймлайн эпиков и карточки ближайших релизов
- Списания по дням, модалки сотрудников и задач
- Теги рисков задач (задержка, неактивность, риск не успеть, Fix Version)
- Рейтинги по категориям спринта

## Требования

- Python 3.11+ (желательно)
- Доступ к Jira и/или GitLab (VPN, если нужен)
- Токены API в `.env`

## Быстрый старт

```bash
# 1. Зависимости
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Секреты
cp .env.example .env
# заполните GITLAB_URL / GITLAB_TOKEN / JIRA_URL / JIRA_TOKEN …

# 3. Конфиг команды
cp team.json.example team.json
# заполните people, jira.boards/projects, gitlab_projects

# 4. Запуск
python run.py
```

После старта откроется UI (по умолчанию `http://127.0.0.1:8765/`).  
Кнопка **«Обновить данные»** пересобирает отчёт без перезапуска сервера.

Только UI на уже собранном `data/report.json` (без Jira/GitLab и без publish):

```bash
python run.py --no-collect
```

### Демо без API

```bash
python run.py --mock
```

Демо **специально не использует** ваш локальный `team.json` с реальными ФИО.  
Берётся анонимный шаблон `team.json.example` + синтетические задачи/MR.

Если нужно проверить демо на своей структуре направлений — временно подставьте данные в `team.json.example` (без реальных сотрудников) или скопируйте example поверх `team.json` только для локальных экспериментов.

### Только JSON без UI

```bash
python run.py --dump-only
# или
python run.py --mock --dump-only
```

Файлы пишутся в `data/raw.json` и `data/report.json`.

### Публикация на home-server

Сбор на Mac (VPN → Jira/GitLab), на сервер уходит только UI + `report.json`:

```bash
python run.py --publish        # сбор + валидация + выгрузка
python run.py --publish-only   # выгрузить уже готовый data/report.json
```

Настройки в `.env`:

```bash
PUBLISH_SSH=server
PUBLISH_REMOTE_DIR=/home/USER/path/to/sprint-report
# optional: printed after successful publish
# PUBLISH_PUBLIC_URL=https://your.domain.example/report/
```

`raw.json` на сервер не отправляется и после успешного `--publish` удаляется локально.

### Автопубликация на Mac (VPN + launchd)

LaunchAgent опрашивает раз в **2 минуты**, но публикует не чаще чем раз в **60 минут**, и только в окне **09:00–19:00** (локальное время). Jira/VPN проверяются только когда публикация уже «должна» произойти (прошло ≥60 мин с прошлого успеха). Переопределение: `PUBLISH_EVERY_SEC` в окружении.

```bash
chmod +x scripts/publish-if-vpn.sh scripts/install-launch-agent.sh
./scripts/install-launch-agent.sh
```

- Лог: `~/Library/Logs/work-reporter.log`
- Метка последнего успеха: `~/Library/Logs/work-reporter.last-publish`
- Ручной прогон: `./scripts/publish-if-vpn.sh`
- Снять агент: `launchctl bootout gui/$(id -u)/com.work-reporter.publish && rm -f ~/Library/LaunchAgents/com.work-reporter.publish.plist`

### Telegram-уведомления (только с сервера)

После успешного/неуспешного `--publish` Mac по SSH вызывает скрипт на сервере; **токен Telegram и вызов Bot API только на сервере**.

На сервере: `~/work-reporter-notify/` (шаблон в [`scripts/server-notify/`](scripts/server-notify/)).

В Mac `.env`:

```bash
PUBLISH_NOTIFY_SCRIPT=~/work-reporter-notify/notify-telegram.sh
```

## Конфигурация

| Файл | Назначение | В git? |
|---|---|---|
| `.env` | URL и токены, норма часов, host/port | нет (`.gitignore`) |
| `team.json` | Команда, доски, проекты, пороги метрик | нет (`.gitignore`) |
| `team.json.example` | Анонимный шаблон | да |
| `.env.example` | Шаблон секретов | да |
| `data/` | Кэш сырых данных и отчёта | нет (создаётся при запуске) |

### Что настраивается в `team.json`

Скопируйте `team.json.example` → `team.json` и заполните. Ниже — смысл каждого блока.

#### `directions`

Справочник направлений команды. Порядок ключей = порядок блоков в UI.

| Поле | Пример | Зачем |
|---|---|---|
| `name` | `"Бэкенд"` | Отображаемое имя |
| `short` | `"Бэк"` | Короткое имя в таймлайне эпиков |
| `is_dev` | `true` | Показывать колонку коммитов / dev-метрики |
| `color` | `"#2A53CC"` | Цвет секций эпиков и чипов |

#### `people`

Ростер сотрудников. В отчёт попадают только задачи людей из этого списка.

| Поле | Пример | Зачем |
|---|---|---|
| `name` | `"Иванов Иван Иванович"` | Каноническое ФИО в UI |
| `direction` | `"backend"` | Ключ из `directions` |
| `aliases` | `["Ivan Ivanov", "ivan.ivanov"]` | Имена/логины из Jira и GitLab для сопоставления |

#### `jira`

| Поле | Пример | Зачем |
|---|---|---|
| `projects` | `["DEMO", "OPS"]` | Ключи проектов Jira |
| `boards` | массив досок | Откуда брать спринт/задачи |
| `boards[].id` | `"100"` | Numeric id из URL доски |
| `boards[].primary` | `true` | Основная scrum-доска (окно спринта) |
| `boards[].has_sprints` | `false` | Kanban без Agile sprints |
| `boards[].has_epics` | `true` | Тянуть epic-связи с доски |

#### `gitlab_projects`

Карта `group/project` → направление (`mobile` / `backend` / …).  
Нужна для MR, коммитов и «помощи другим направлениям» в рейтингах.

#### `status_rules`

Какие статусы для направления считаются `active` / `done` (остальное = other).  
Можно задать `default` и переопределения по ключу направления (`mobile`, `qa`, …).

Это влияет на:
- прогресс направления/эпика/релиза;
- список «осталось»;
- теги риска (неактивность / риск не успеть ставятся только на active).

#### `display_task_filters`

Подстроки в названии задачи. Такие задачи **скрываются из UI-списков** (поддержка/прилёты и т.п.), но могут учитываться в агрегатах.

#### `task_tags`

| Поле | По умолчанию | Зачем |
|---|---|---|
| `inactive_days` | `3` | Через сколько дней без updates ставить тег «Неактивная» |

#### `metrics`

Пороги расчёта отчёта:

| Поле | По умолчанию | Зачем |
|---|---|---|
| `release_window_days` | `14` | Релизы: дата в окне `[start спринта … end + N дней]` |
| `slip_tolerance_pp` | `12` | Допуск отставания прогресса задач от календаря релиза (п.п.) |
| `hours_warn_ratio` | `0.5` | Доля от дневной нормы: ниже = «мало списано» |
| `risk_sprint_time_pct` | `70` | После какого % спринта задачи попадают в «риск срыва» |
| `risk_days_left` | `3` | Или если до конца спринта осталось ≤ N дней |
| `stale_days` | `3` | Порог блока «застрявшие» |
| `epic_bar_min_pct` | `28` | Мин. ширина полосы эпика |
| `epic_section_min_pct` | `14` | Мин. ширина секции направления в эпике |
| `risks_limit` | `40` | Сколько рисков отдавать в API |
| `person_tasks_limit` | `40` | Лимит задач в профиле сотрудника |
| `person_active_tasks_limit` | `30` | Лимит active-задач в профиле |

#### `ratings`

| Поле | По умолчанию | Зачем |
|---|---|---|
| `top_n` | `3` | Сколько мест показывать в карточках рейтинга |
| `place_points` | `{1:3,2:2,3:1}` | Очки за места в категории «Топы» |

#### `ui`

| Поле | По умолчанию | Зачем |
|---|---|---|
| `task_table_preview` | `5` | Сколько строк задач видно до кнопки «Показать ещё» |

#### `gitlab_days`

Fallback-окно GitLab (дни), если у спринта нет даты старта. Можно переопределить env `GITLAB_DAYS`.

### Полезные ключи `.env`

```bash
GITLAB_URL=...
GITLAB_TOKEN=...
JIRA_URL=...
JIRA_TOKEN=...
JIRA_EXPECTED_HOURS_PER_DAY=8
HOST=127.0.0.1
PORT=8765
```

Опционально: `GITLAB_DAYS`, `GITLAB_DIRECTION_MAP` (override карты проектов).

## Как это работает

1. `run.py` поднимает локальный HTTP-сервер из `web/`
2. В фоне `collector` тянет Jira/GitLab (или mock)
3. Сырой ответ сохраняется в `data/raw.json`
4. Метрики собираются в `data/report.json`
5. UI (`web/app.js`) читает `/api/report` и рисует отчёт

`data/raw.json` и `data/report.json` **не коммитятся**: каталог `data/` в `.gitignore`, файлы появляются только при запуске сбора.

## Структура репозитория

```
run.py                 # точка входа
team.json.example      # шаблон конфига команды
.env.example           # шаблон секретов
src/                   # сбор данных и расчёт метрик
web/                   # UI отчёта
data/                  # runtime-кэш (локально)
```

## Замечания по безопасности

- Не коммитьте `.env` и `team.json` — там токены и персональные данные команды.
- `data/` может содержать выгрузки Jira/GitLab с ФИО и задачами.
- Для публикации репозитория используйте только `*.example` файлы.
