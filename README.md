# Telegram Personal Assistant — максимально простой запуск

Этот вариант подготовлен для запуска через Railway. Код уже содержит Dockerfile и Railway-конфигурацию.

## Что нужно сделать

1. Создать Telegram-бота через @BotFather и получить `BOT_TOKEN`.
2. Загрузить эту папку в GitHub как новый репозиторий.
3. В Railway выбрать **New Project → Deploy from GitHub Repo** и выбрать этот репозиторий.
4. Railway автоматически увидит `Dockerfile` и соберёт приложение.
5. В Railway открыть **Variables** и добавить:

`BOT_TOKEN` = токен от @BotFather

Также можно добавить:
- `TIMEZONE=Europe/Moscow`
- `CHECK_MINUTES=10`
- `MORNING_TIME=07:30`
- `TRAVEL_MINUTES=40`
- `BUFFER_MINUTES=5`

6. Нажать Deploy.
7. Открыть своего бота в Telegram и отправить `/start`.

## Команды

/start — подключить бота
/today — пары на сегодня
/cross — пересечения с Э-157/1 и ЭП-161
/cross all — все найденные пересечения
/check — проверить новое расписание сейчас
/leave — время выхода
/travel 40 — дорога 40 минут
/buffer 5 — запас 5 минут

## Важно

Токен Telegram не нужно вставлять в код или отправлять мне. Он вводится только в Railway → Variables.

Railway запускает приложение как постоянно работающий сервис, поэтому бот может самостоятельно проверять расписание каждые 10 минут и отправлять уведомления.
