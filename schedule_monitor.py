from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urljoin, unquote

import aiohttp
from bs4 import BeautifulSoup

from schedule_parser import canonical_schedule, compare_schedules, parse_vstu_groups_xls

SOURCE_PAGE_URL = "https://www.vstu.ru/student/raspisaniya/"
FALLBACK_XLS_URL = "https://www.vstu.ru/upload/raspisanie/z/%D0%9E%D0%9D_%D0%A4%D0%AD%D0%A3_1%20%D0%BA%D1%83%D1%80%D1%81.xls"
GROUP = "ЭР-153"
COMPARE_GROUPS = ["ЭР-153", "Э-157/1", "ЭП-161"]
DATA_DIR = Path("data")
STATE_FILE = DATA_DIR / "schedule_state.json"
LATEST_FILE = DATA_DIR / "latest_schedule.xls"


def load_state() -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def find_schedule_url(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = " ".join(a.stripped_strings)
        absolute = urljoin(SOURCE_PAGE_URL, href)
        low = unquote(f"{absolute} {text}").lower()
        if absolute.lower().split("?")[0].endswith((".xls", ".xlsx")):
            candidates.append((absolute, low))

    preferred = [x for x in candidates if "фэу" in x[1] and "1 курс" in x[1]]
    if preferred:
        return preferred[0][0]
    if candidates:
        return candidates[0][0]
    return FALLBACK_XLS_URL


async def download_current_schedule(session: aiohttp.ClientSession) -> tuple[bytes, str]:
    async with session.get(SOURCE_PAGE_URL, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        resp.raise_for_status()
        html = await resp.text()
    url = find_schedule_url(html)
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
        resp.raise_for_status()
        data = await resp.read()
    return data, url


def format_event(e: dict) -> str:
    bits = [f"{e['date']} · {e['slot']} — {e['subject']}"]
    if e.get("room"):
        bits.append(f"ауд. {e['room']}")
    if e.get("teacher"):
        bits.append(e["teacher"])
    return " | ".join(bits)


def format_changes(changes: dict) -> str:
    lines = ["⚠️ Расписание ЭР-153 изменилось", ""]
    if changes["added"]:
        lines.append("➕ Добавлено:")
        lines.extend("• " + format_event(e) for e in changes["added"])
        lines.append("")
    if changes["removed"]:
        lines.append("➖ Убрано:")
        lines.extend("• " + format_event(e) for e in changes["removed"])
        lines.append("")
    if changes["changed"]:
        lines.append("✏️ Изменено:")
        for pair in changes["changed"]:
            old, new = pair["old"], pair["new"]
            lines.append(f"• {new['date']} · {new['slot']}")
            if old.get("subject") != new.get("subject"):
                lines.append(f"  Предмет: {old.get('subject')} → {new.get('subject')}")
            if old.get("teacher") != new.get("teacher"):
                lines.append(f"  Преподаватель: {old.get('teacher') or '—'} → {new.get('teacher') or '—'}")
            if old.get("room") != new.get("room"):
                lines.append(f"  Аудитория: {old.get('room') or '—'} → {new.get('room') or '—'}")
        lines.append("")
    lines.append("Источник: официальный файл расписания ВолгГТУ.")
    return "\n".join(lines).strip()


async def check_schedule(session: aiohttp.ClientSession) -> dict:
    data, source_url = await download_current_schedule(session)
    file_hash = hashlib.sha256(data).hexdigest()
    LATEST_FILE.write_bytes(data)
    parsed = parse_vstu_groups_xls(str(LATEST_FILE), COMPARE_GROUPS)
    events = canonical_schedule(parsed[GROUP])
    new_state = {"hash": file_hash, "source_url": source_url, "events": events, "comparison_groups": {g: canonical_schedule(parsed[g]) for g in COMPARE_GROUPS}}
    old_state = load_state()

    if not old_state or "events" not in old_state:
        save_state(new_state)
        return {"first_run": True, "changed": False, "source_url": source_url, "changes": None}

    changes = compare_schedules(old_state["events"], events)
    changed = any(changes.values())
    save_state(new_state)
    return {"first_run": False, "changed": changed, "source_url": source_url, "changes": changes}
