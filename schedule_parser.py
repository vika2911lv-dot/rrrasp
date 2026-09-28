from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import xlrd

GROUP = "ЭР-153"
SEMESTER_START = date(2026, 9, 1)
SEMESTER_END = date(2026, 12, 31)
MONTH_COLUMNS = {0: 9, 1: 10, 2: 11, 3: 12}
WEEKDAYS = {"ПОНЕДЕЛЬНИК": 0, "ВТОРНИК": 1, "СРЕДА": 2, "ЧЕТВЕРГ": 3, "ПЯТНИЦА": 4, "СУББОТА": 5}
SLOT_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")
DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})(?!\d)")
TEACHER_RE = re.compile(r"(?:проф\.|доц\.|ст\.\s*преп\.)?\s*[А-ЯЁ][а-яё-]+\s+[А-ЯЁ]\.[А-ЯЁ]?\.?")
ROOM_RE = re.compile(r"^(?:[А-ЯЁ]{1,3}-?\d+[а-яёА-ЯЁ]?|\d+[а-яёА-ЯЁ]?\s*,.*)$")
HOUR_TIMES = {
    1: ("08:30", "09:15"), 2: ("09:20", "10:00"), 3: ("10:10", "10:55"), 4: ("11:00", "11:40"),
    5: ("11:50", "12:35"), 6: ("12:40", "13:20"), 7: ("13:40", "14:25"), 8: ("14:30", "15:10"),
    9: ("15:20", "16:05"), 10: ("16:10", "16:50"), 11: ("17:00", "17:45"), 12: ("17:50", "18:30"),
}


def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip() if value is not None else ""


class SheetAccessor:
    def __init__(self, sheet):
        self.sheet = sheet
        self.merged = list(sheet.merged_cells)

    def value(self, row: int, col: int) -> Any:
        for rlo, rhi, clo, chi in self.merged:
            if rlo <= row < rhi and clo <= col < chi:
                return self.sheet.cell_value(rlo, clo)
        return self.sheet.cell_value(row, col)

    def merged_end(self, row: int, col_start: int, col_end: int) -> int:
        end = row
        for rlo, rhi, clo, chi in self.merged:
            if chi <= col_start or clo > col_end:
                continue
            if rlo <= row < rhi:
                end = max(end, rhi - 1)
        return end


def is_teacher(text: str) -> bool:
    text = norm(text)
    return bool(TEACHER_RE.search(text)) or bool(re.match(r"^(?:проф\.|доц\.|ст\.\s*преп\.)\s*[А-ЯЁ][а-яё-]+", text, re.I))


def is_room(text: str) -> bool:
    text = norm(text)
    low = text.lower()
    if "фитнес-зал" in low or "кор." in low:
        return True
    return bool(ROOM_RE.match(text))


def parse_dates_in_text(text: str) -> list[date]:
    result = []
    for day_s, month_s in DATE_RE.findall(text):
        try:
            dt = date(2026, int(month_s), int(day_s))
        except ValueError:
            continue
        if SEMESTER_START <= dt <= SEMESTER_END:
            result.append(dt)
    return result


def find_group_columns(sheet, groups: list[str]) -> dict[str, int]:
    found = {}
    for col in range(sheet.ncols):
        value = norm(sheet.cell_value(11, col))
        if value in groups:
            found[value] = col
    missing = [g for g in groups if g not in found]
    if missing:
        raise ValueError(f"В файле не найдены группы: {', '.join(missing)}")
    return found


def find_all_group_columns(sheet) -> dict[str, int]:
    result = {}
    for col in range(sheet.ncols):
        value = norm(sheet.cell_value(11, col))
        if value:
            result[value] = col
    return result


def _detect_block_boundaries(accessor: SheetAccessor) -> list[tuple[int, int]]:
    starts = []
    for r in range(accessor.sheet.nrows):
        if norm(accessor.sheet.cell_value(r, 4)).upper() in WEEKDAYS:
            starts.append(r)
    if not starts:
        raise ValueError("Не найдены дни недели в файле расписания")
    boundaries = []
    current = starts[0]
    previous = starts[0]
    for s in starts[1:]:
        if s - previous > 15:
            boundaries.append((current, s))
            current = s
        previous = s
    boundaries.append((current, accessor.sheet.nrows))
    return boundaries[:2]


def parse_block(accessor: SheetAccessor, start_row: int, end_row: int, parity: int, group_col: int) -> list[dict]:
    day = None
    slots = []
    for r in range(start_row, end_row):
        day_text = norm(accessor.sheet.cell_value(r, 4)).upper()
        if day_text in WEEKDAYS:
            day = day_text
        slot_text = norm(accessor.sheet.cell_value(r, 5))
        if day and SLOT_RE.match(slot_text):
            slots.append((r, day, slot_text))

    group_end_col = min(group_col + 3, accessor.sheet.ncols - 1)
    events = []
    for i, (r, day_name, slot_text) in enumerate(slots):
        next_same_day = next((rr for rr, dd, _ in slots[i + 1:] if dd == day_name), None)
        base_end = (next_same_day - 1) if next_same_day is not None else min(r + 4, end_row - 1)
        merged_end = accessor.merged_end(r, group_col, group_end_col)
        end = max(base_end, merged_end)
        if merged_end > base_end:
            end = min(merged_end + 2, end_row - 1)

        values = []
        for rr in range(r, end + 1):
            for cc in range(group_col, group_end_col + 1):
                value = norm(accessor.value(rr, cc))
                if value and value not in values:
                    values.append(value)

        explicit_dates = []
        for cc, month in MONTH_COLUMNS.items():
            raw = accessor.sheet.cell_value(r, cc)
            if isinstance(raw, (int, float)) and raw:
                try:
                    explicit_dates.append(date(2026, month, int(raw)))
                except ValueError:
                    pass
        for value in values:
            explicit_dates.extend(parse_dates_in_text(value))
        explicit_dates = sorted(set(d for d in explicit_dates if SEMESTER_START <= d <= SEMESTER_END))

        subject = None
        teachers, rooms = [], []
        for value in values:
            if value in {GROUP} or DATE_RE.search(value):
                continue
            if is_teacher(value):
                teachers.append(value)
            elif is_room(value):
                rooms.append(value)
            elif subject is None:
                subject = value

        if not subject:
            continue
        a, b = SLOT_RE.match(slot_text).groups()
        events.append({
            "weekday": day_name,
            "weekday_index": WEEKDAYS[day_name],
            "slot": slot_text.replace(" ", ""),
            "hour_start": int(a), "hour_end": int(b),
            "subject": subject,
            "teacher": "; ".join(dict.fromkeys(teachers)),
            "room": "; ".join(dict.fromkeys(rooms)),
            "week_parity": parity,
            "explicit_dates": [d.isoformat() for d in explicit_dates],
        })
    return events


def _infer_parity(events: list[dict], fallback: int) -> int:
    explicit = [date.fromisoformat(x) for e in events for x in e["explicit_dates"]]
    if not explicit:
        return fallback
    counts = {0: 0, 1: 0}
    for dt in explicit:
        counts[dt.isocalendar().week % 2] += 1
    return 0 if counts[0] >= counts[1] else 1


def expand_events(raw_events: list[dict]) -> list[dict]:
    result, seen = [], set()
    for event in raw_events:
        if event["explicit_dates"]:
            dates = [date.fromisoformat(x) for x in event["explicit_dates"]]
        else:
            dates = []
            cur = SEMESTER_START
            while cur <= SEMESTER_END:
                if cur.weekday() == event["weekday_index"] and cur.isocalendar().week % 2 == event["week_parity"]:
                    dates.append(cur)
                cur += timedelta(days=1)
        for dt in dates:
            item = dict(event)
            item.pop("explicit_dates", None)
            item["date"] = dt.isoformat()
            item["start_time"] = HOUR_TIMES[event["hour_start"]][0]
            item["end_time"] = HOUR_TIMES[event["hour_end"]][1]
            key = (item["date"], item["slot"], item["subject"], item["teacher"], item["room"])
            if key not in seen:
                seen.add(key)
                result.append(item)
    return sorted(result, key=lambda x: (x["date"], x["hour_start"], x["subject"]))


def parse_vstu_xls(path: str | Path, group: str = GROUP) -> list[dict]:
    return parse_vstu_groups_xls(path, [group]).get(group, [])


def parse_vstu_groups_xls(path: str | Path, groups: list[str]) -> dict[str, list[dict]]:
    book = xlrd.open_workbook(str(path), formatting_info=False)
    sheet = book.sheet_by_index(0)
    accessor = SheetAccessor(sheet)
    columns = find_group_columns(sheet, groups)
    boundaries = _detect_block_boundaries(accessor)
    output = {g: [] for g in groups}
    for group, col in columns.items():
        raw_all = []
        for idx, (start, end) in enumerate(boundaries):
            raw = parse_block(accessor, start, end, parity=idx, group_col=col)
            actual_parity = _infer_parity(raw, fallback=idx)
            for event in raw:
                event["week_parity"] = actual_parity
            raw_all.extend(raw)
        output[group] = expand_events(raw_all)
    return output


def canonical_schedule(events: list[dict]) -> list[dict]:
    fields = ["date", "slot", "subject", "teacher", "room", "start_time", "end_time"]
    return [{k: e.get(k, "") for k in fields} for e in events]


def compare_schedules(old: list[dict], new: list[dict]) -> dict:
    def key(e): return (e.get("date", ""), e.get("slot", ""))
    old_map, new_map = {key(e): e for e in old}, {key(e): e for e in new}
    added = [new_map[k] for k in sorted(new_map.keys() - old_map.keys())]
    removed = [old_map[k] for k in sorted(old_map.keys() - new_map.keys())]
    fields = ["subject", "teacher", "room", "start_time", "end_time"]
    changed = []
    for k in sorted(old_map.keys() & new_map.keys()):
        o, n = old_map[k], new_map[k]
        if any(norm(o.get(f)) != norm(n.get(f)) for f in fields):
            changed.append({"old": o, "new": n})
    return {"added": added, "removed": removed, "changed": changed}
