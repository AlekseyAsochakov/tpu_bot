# parser.py
import logging
import re
from datetime import datetime, timezone, timedelta

from bot.config import config


def extract_group_info(url: str) -> tuple[str, int, str] | None:
    """Извлекает (group_id, year, domain) из URL типа .../gruppa_42624/2025/1/view.html"""
    match = re.search(r'https?://([^/]+).*?gruppa_(\d+).*?(\d{4})', url)
    if match:
        return match.group(2), int(match.group(3)), match.group(1)
    return None


def get_current_academic_year() -> int:
    """Определяет год начала текущего учебного года (с 1 сентября)."""
    now = datetime.now()
    if now.month < 9:
        return now.year - 1
    return now.year

def get_current_study_week(year: int = None) -> int:
    """
    Считает текущую учебную неделю.
    Учебный год начинается 1 сентября.
    """
    tomsk_tz = timezone(timedelta(hours=7))
    now = datetime.now(tomsk_tz)

    if year is None:
        year = get_current_academic_year()

    # 1 сентября учебного года
    start = datetime(year, 9, 1, tzinfo=tomsk_tz)

    delta = now - start
    days = delta.days

    if days < 0:
        return 1

    return (days // 7) + 1


def build_schedule_url_by_group(group_id: str, week: int = None, domain: str = "ro-rasp.tpu.ru") -> str:
    year = get_current_academic_year()
    current_week = week if week else get_current_study_week(year)
    return f"https://{domain}/gruppa_{group_id}/{year}/{current_week}/view.html"

def build_schedule_url(group_id: str, year: int, week: int, domain: str = "ro-rasp.tpu.ru") -> str:
    return f"https://{domain}/gruppa_{group_id}/{year}/{week}/view.html"


import httpx
import logging
from bot.config import config

async def parse_whole_week(group_url: str) -> dict[int, list[dict]]:
    """Calls the standalone Parser API service."""
    parser_url = getattr(config, "PARSER_SERVICE_URL", "http://parser:8001")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{parser_url}/parse", params={"url": group_url})
            response.raise_for_status()
            # Parser API returns keys as strings ("1", "2"...), we need ints
            data = response.json()
            return {int(k): v for k, v in data.items()}
    except Exception as e:
        logging.error(f"Error calling Parser API: {e}")
        return {}

async def parse_tpu_schedule(group_url: str, day_of_week: int) -> str:
    # Keep the formatting logic but use the new parser caller
    days_names = {
        1: "Понедельник", 2: "Вторник", 3: "Среда",
        4: "Четверг", 5: "Пятница", 6: "Суббота", 7: "Воскресенье"
    }
    day_name_rus = days_names.get(day_of_week, "Понедельник")

    if day_of_week == 7:
        return f"📅 <b>{day_name_rus}:</b>\n\nСегодня воскресенье, пар нет! 🎉"

    try:
        week_data = await parse_whole_week(group_url)
        lessons = week_data.get(day_of_week, [])

        if lessons:
            text = f"📅 <b>Расписание ({day_name_rus}):</b>\n\n"
            for lesson in lessons:
                text += f"⏰ <b>{lesson['time']}</b>\n"
                text += f"📖 Предмет: {lesson['subject']}\n"
                if lesson.get('type'):
                    text += f"📚 Тип занятия: {lesson['type']}\n"
                if lesson.get('teacher'):
                    text += f"👤 Преподаватель: {lesson['teacher']}\n"
                if lesson.get('room'):
                    text += f"📍 Кабинет: {lesson['room']}\n"
                if lesson.get('other'):
                    text += f"📝 {lesson['other']}\n"
                text += "─────────────────────\n"
            return text
        else:
            return f"📅 <b>{day_name_rus}:</b>\n\nСегодня пар нет! Отдыхай! 🎉"

    except Exception as e:
        logging.error(f"Schedule parsing error: {e}")
        return f"❌ Ошибка загрузки расписания."
