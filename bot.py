from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from schedule_monitor import check_schedule, format_changes, load_state

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
CHECK_MINUTES = int(os.getenv("CHECK_MINUTES", "10"))
MORNING_TIME = os.getenv("MORNING_TIME", "07:30")
DEFAULT_TRAVEL_MINUTES = int(os.getenv("TRAVEL_MINUTES", "40"))
DEFAULT_BUFFER_MINUTES = int(os.getenv("BUFFER_MINUTES", "5"))
DATA_DIR = Path("data")
USERS_FILE = DATA_DIR / "users.json"
SETTINGS_FILE = DATA_DIR / "user_settings.json"

if not BOT_TOKEN:
    raise RuntimeError("Укажите BOT_TOKEN в .env")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone=TZ)
monitor_lock = asyncio.Lock()


def users() -> list[int]:
    DATA_DIR.mkdir(exist_ok=True)
    if not USERS_FILE.exists():
        return []
    return json.loads(USERS_FILE.read_text(encoding="utf-8"))


def save_user(chat_id: int) -> None:
    ids = set(users())
    ids.add(chat_id)
    USERS_FILE.write_text(json.dumps(sorted(ids)), encoding="utf-8")


def settings() -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    if not SETTINGS_FILE.exists():
        return {}
    return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))


def get_settings(chat_id: int) -> dict:
    all_settings = settings()
    current = all_settings.get(str(chat_id), {})
    return {
        "travel": int(current.get("travel", DEFAULT_TRAVEL_MINUTES)),
        "buffer": int(current.get("buffer", DEFAULT_BUFFER_MINUTES)),
    }


def update_settings(chat_id: int, **updates) -> dict:
    all_settings = settings()
    current = get_settings(chat_id)
    current.update({k: int(v) for k, v in updates.items()})
    all_settings[str(chat_id)] = current
    SETTINGS_FILE.write_text(json.dumps(all_settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def departure_time(start_time: str, travel: int, buffer: int) -> str:
    dt = datetime.strptime(start_time, "%H:%M")
    dt -= timedelta(minutes=travel + buffer)
    return dt.strftime("%H:%M")


def event_lines(events: list[dict]) -> list[str]:
    lines = []
    for e in events:
        lines.append(f"{e['start_time']}–{e['end_time']} · {e['subject']}")
        if e.get("room"):
            lines.append(f"  Аудитория: {e['room']}")
        if e.get("teacher"):
            lines.append(f"  Преподаватель: {e['teacher']}")
    return lines


def cross_text(state: dict, days: int | None = 14) -> str:
    groups = state.get("comparison_groups", {})
    mine = groups.get("ЭР-153", [])
    targets = ["Э-157/1", "ЭП-161"]
    if not mine or not any(groups.get(g) for g in targets):
        return "Сначала выполните /check — бот загрузит расписание трёх групп."

    today = datetime.now(TZ).date()
    end = None if days is None else today + timedelta(days=days - 1)
    mine_map = {(e["date"], e["slot"]): e for e in mine}
    result = []
    for target in targets:
        other = groups.get(target, [])
        other_map = {(e["date"], e["slot"]): e for e in other}
        keys = sorted(set(mine_map) & set(other_map))
        for key in keys:
            dt = datetime.fromisoformat(key[0]).date()
            if dt < today or (end is not None and dt > end):
                continue
            a, b = mine_map[key], other_map[key]
            result.append((dt, key[1], target, a, b))

    if not result:
        period = "весь сохранённый семестр" if days is None else f"ближайшие {days} дней"
        return f"Пересечений ЭР-153 с Э-157/1 и ЭП-161 за {period} не найдено."

    lines = ["Пересечения с другими группами:", ""]
    current_date = None
    for dt, slot, target, mine_e, other_e in result:
        if dt != current_date:
            if current_date is not None:
                lines.append("")
            lines.append(dt.strftime("%d.%m.%Y") + ":")
            current_date = dt
        lines.append(f"• {slot} · {mine_e['start_time']}–{mine_e['end_time']} · {target}")
        lines.append(f"  ЭР-153: {mine_e['subject']}" + (f" · {mine_e['room']}" if mine_e.get("room") else ""))
        lines.append(f"  {target}: {other_e['subject']}" + (f" · {other_e['room']}" if other_e.get("room") else ""))
    return "\n".join(lines)


@dp.message(Command("start"))
async def start(message: Message):
    save_user(message.chat.id)
    s = get_settings(message.chat.id)
    await message.answer(
        "Бот подключён к расписанию ЭР-153.\n\n"
        "Он проверяет актуальный файл ВолгГТУ и уведомляет только о реальных изменениях ЭР-153.\n\n"
        f"Сейчас дорога: {s['travel']} мин, запас: {s['buffer']} мин.\n\n"
        "/check — проверить расписание сейчас\n"
        "/today — пары на сегодня + время выхода\n"
        "/travel 40 — задать время дороги\n"
        "/buffer 5 — задать запас времени\n"
        "/leave — показать, во сколько выходить на первую пару\n"
        "/cross — пересечения с Э-157/1 и ЭП-161 на 14 дней\n"
        "/cross all — пересечения на весь сохранённый семестр"
    )


@dp.message(Command("check"))
async def manual_check(message: Message):
    save_user(message.chat.id)
    async with aiohttp.ClientSession() as session:
        result = await check_schedule(session)
    if result["first_run"]:
        await message.answer("Текущее расписание сохранено как исходная версия. Теперь бот сможет отслеживать изменения ЭР-153 и пересечения с Э-157/1 и ЭП-161.")
    elif result["changed"]:
        await message.answer(format_changes(result["changes"]))
    else:
        await message.answer("Изменений в расписании ЭР-153 нет.")


@dp.message(Command("travel"))
async def travel(message: Message):
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit() or not 1 <= int(parts[1]) <= 180:
        await message.answer("Например: /travel 40")
        return
    s = update_settings(message.chat.id, travel=int(parts[1]))
    await message.answer(f"Время дороги установлено: {s['travel']} мин. Время выхода будет рассчитываться автоматически.")


@dp.message(Command("buffer"))
async def buffer_cmd(message: Message):
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit() or not 0 <= int(parts[1]) <= 60:
        await message.answer("Например: /buffer 5")
        return
    s = update_settings(message.chat.id, buffer=int(parts[1]))
    await message.answer(f"Запас установлен: {s['buffer']} мин.")


@dp.message(Command("leave"))
async def leave(message: Message):
    save_user(message.chat.id)
    state = load_state()
    today_str = datetime.now(TZ).date().isoformat()
    events = sorted([e for e in state.get("events", []) if e["date"] == today_str], key=lambda e: e["start_time"])
    if not events:
        await message.answer("Сегодня занятий по сохранённому расписанию нет.")
        return
    first = events[0]
    s = get_settings(message.chat.id)
    leave = departure_time(first["start_time"], s["travel"], s["buffer"])
    await message.answer(
        f"Первая пара сегодня: {first['start_time']} · {first['subject']}\n"
        f"Аудитория: {first.get('room') or '—'}\n\n"
        f"🚶 Выйти не позднее {leave}\n"
        f"Дорога: {s['travel']} мин + запас: {s['buffer']} мин."
    )


@dp.message(Command("today"))
async def today(message: Message):
    save_user(message.chat.id)
    state = load_state()
    today_str = datetime.now(TZ).date().isoformat()
    events = sorted([e for e in state.get("events", []) if e["date"] == today_str], key=lambda e: e["start_time"])
    if not events:
        await message.answer("Сегодня занятий по сохранённому расписанию нет.")
        return
    s = get_settings(message.chat.id)
    leave = departure_time(events[0]["start_time"], s["travel"], s["buffer"])
    lines = ["Расписание ЭР-153 на сегодня:", "", f"🚶 Выйти не позднее {leave} (дорога {s['travel']} мин + запас {s['buffer']} мин)", ""]
    lines.extend(event_lines(events))
    await message.answer("\n".join(lines))


@dp.message(Command("cross"))
async def cross(message: Message):
    save_user(message.chat.id)
    state = load_state()
    days = None if len(message.text.split()) > 1 and message.text.split()[1].lower() == "all" else 14
    await message.answer(cross_text(state, days))


async def morning_message(chat_id: int):
    state = load_state()
    today_str = datetime.now(TZ).date().isoformat()
    events = sorted([e for e in state.get("events", []) if e["date"] == today_str], key=lambda e: e["start_time"])
    if not events:
        await bot.send_message(chat_id, "Сегодня по расписанию ЭР-153 занятий нет. Можно не ставить ранний будильник.")
        return
    s = get_settings(chat_id)
    leave = departure_time(events[0]["start_time"], s["travel"], s["buffer"])
    lines = [
        "Доброе утро. Расписание ЭР-153 на сегодня:", "",
        f"🚶 Выйти не позднее {leave}",
        f"Дорога: {s['travel']} мин · запас: {s['buffer']} мин", ""
    ]
    lines.extend(event_lines(events))
    await bot.send_message(chat_id, "\n".join(lines))


async def send_morning():
    for chat_id in users():
        try:
            await morning_message(chat_id)
        except Exception:
            pass


async def background_check():
    async with monitor_lock:
        try:
            async with aiohttp.ClientSession() as session:
                result = await check_schedule(session)
            if result["changed"]:
                text = format_changes(result["changes"])
                for chat_id in users():
                    try:
                        await bot.send_message(chat_id, text)
                    except Exception:
                        pass
        except Exception as exc:
            print(f"schedule check error: {exc}")


async def main():
    scheduler.add_job(background_check, "interval", minutes=CHECK_MINUTES, id="schedule_check", replace_existing=True)
    hour, minute = map(int, MORNING_TIME.split(":"))
    scheduler.add_job(send_morning, "cron", hour=hour, minute=minute, id="morning", replace_existing=True)
    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
