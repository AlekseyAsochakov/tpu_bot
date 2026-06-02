# base.py
import logging
from datetime import datetime, timezone, timedelta
from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

from bot.config import config
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.database.db import AsyncSessionLocal
from bot.database.crud import get_or_create_user, update_user_group
from bot.services.parser import (
    parse_tpu_schedule,
    extract_group_info,
    get_current_study_week,
    build_schedule_url,
    build_schedule_url_by_group,
)

base_router = Router()


class RegistrationState(StatesGroup):
    waiting_for_start = State()
    waiting_for_link = State()


# === РАСПИСАНИЕ ЗВОНКОВ ТПУ ===
TPU_PAIRS = [
    ("1-я пара", datetime.strptime("08:30", "%H:%M").time(), datetime.strptime("10:05", "%H:%M").time()),
    ("2-я пара", datetime.strptime("10:25", "%H:%M").time(), datetime.strptime("12:00", "%H:%M").time()),
    ("3-я пара", datetime.strptime("12:40", "%H:%M").time(), datetime.strptime("14:15", "%H:%M").time()),
    ("4-я пара", datetime.strptime("14:35", "%H:%M").time(), datetime.strptime("16:10", "%H:%M").time()),
    ("5-я пара", datetime.strptime("16:30", "%H:%M").time(), datetime.strptime("18:05", "%H:%M").time()),
    ("6-я пара", datetime.strptime("18:25", "%H:%M").time(), datetime.strptime("20:00", "%H:%M").time()),
]


def get_bell_status() -> str:
    tomsk_tz = timezone(timedelta(hours=7))
    now = datetime.now(tomsk_tz)
    current_time = now.time()

    for i, (name, start, end) in enumerate(TPU_PAIRS):
        end_dt = datetime.combine(now.date(), end, tzinfo=tomsk_tz)
        start_dt = datetime.combine(now.date(), start, tzinfo=tomsk_tz)

        if start <= current_time <= end:
            remaining = end_dt - now
            minutes = remaining.seconds // 60
            return (
                f"⏰ Сейчас идёт <b>{name}</b>\n"
                f"🕒 До конца пары: <b>{minutes // 60} ч {minutes % 60} мин</b>"
            )
        elif current_time < start:
            delta = start_dt - now
            minutes = delta.seconds // 60
            return (
                f"☕ Перерыв\n"
                f"⏳ До начала <b>{name}</b>: <b>{minutes // 60} ч {minutes % 60} мин</b>"
            )

    return "🏠 Пары на сегодня закончились! Можно отдыхать 🎉"


def get_start_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🚀 Start")]],
        resize_keyboard=True,
        input_field_placeholder="Нажми Start, чтобы начать..."
    )


def get_main_keyboard():
    buttons = [
        [KeyboardButton(text="📅 Расписание на сегодня"), KeyboardButton(text="🔔 Расписание пар")],
        [KeyboardButton(text="📝 Добавить дедлайн"), KeyboardButton(text="📚 Мои дедлайны")],
        [KeyboardButton(text="📅 Экспорт в календарь"), KeyboardButton(text="⚙️ Сменить ссылку расписания")]
    ]

    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        input_field_placeholder="Выберите действие..."
    )


def get_cancel_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )


# === ПЕРВЫЙ ВХОД ===
@base_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()

    async with AsyncSessionLocal() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.full_name)

    # Force send new keyboard to refresh user's UI
    await message.answer(
        "Перезагружаю меню...",
        reply_markup=get_main_keyboard()
    )

    if not user.group_name:
        await message.answer(
            f"Привет, {user.full_name}! 👋\n"
            f"Я твой виртуальный ассистент ТПУ.\n\n"
            f"Нажми <b>🚀 Start</b>, чтобы начать!",
            reply_markup=get_start_keyboard(),
            parse_mode="HTML"
        )
        await state.set_state(RegistrationState.waiting_for_start)
    else:
        await message.answer(
            f"Рад видеть тебя снова! 👋",
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )


# === НАЖАТИЕ START (первый вход) ===
@base_router.message(RegistrationState.waiting_for_start, F.text == "🚀 Start")
async def process_start_btn(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Выбери действие в меню ниже. Для просмотра расписания нажми «📅 Расписание на сегодня».",
        reply_markup=get_main_keyboard()
    )


# === FALLBACK: Start нажат вне регистрации ===
@base_router.message(F.text == "🚀 Start", StateFilter(None))
async def fallback_start(message: Message, state: FSMContext):
    await state.clear()
    async with AsyncSessionLocal() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.full_name)

    if not user.group_name:
        await message.answer(
            f"Привет, {user.full_name}! 👋\n"
            f"Я твой виртуальный ассистент ТПУ.\n\n"
            f"Нажми <b>🚀 Start</b>, чтобы начать!",
            reply_markup=get_start_keyboard(),
            parse_mode="HTML"
        )
        await state.set_state(RegistrationState.waiting_for_start)
    else:
        await message.answer(
            f"Рад видеть тебя снова! 👋",
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )


# === РАСПИСАНИЕ ПАР (ЗВОНКИ) ===
@base_router.message(F.text.contains("Расписание пар"), StateFilter("*"))
async def process_bells_btn(message: Message, state: FSMContext):
    await state.clear()
    text = "📋 <b>Расписание звонков ТПУ:</b>\n\n"
    for name, start, end in TPU_PAIRS:
        text += f"🕐 <b>{name}:</b> {start.strftime('%H:%M')} – {end.strftime('%H:%M')}\n"

    text += f"\n{'─' * 30}\n"
    text += get_bell_status()

    await message.answer(text, parse_mode="HTML", reply_markup=get_main_keyboard())


# === РАСПИСАНИЕ НА СЕГОДНЯ ===
@base_router.message(F.text.contains("Расписание на сегодня"), StateFilter("*"))
async def process_schedule_btn(message: Message, state: FSMContext):
    from bot.services.metrics import USER_INTERACTION_COUNT
    from bot.main import redis_conn
    USER_INTERACTION_COUNT.labels(command="schedule").inc()

    async with AsyncSessionLocal() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.full_name)

        if not user.group_name:
            await message.answer(
                "⚠️ У тебя пока не указана группа или ссылка на расписание.\n\n"
                "Пришли мне номер своей группы (например: 42624) или ссылку на расписание с сайта https://rasp.tpu.ru\n\n"
                "Или нажми «❌ Отмена».",
                parse_mode="HTML",
                reply_markup=get_cancel_keyboard()
            )
            await state.set_state(RegistrationState.waiting_for_link)
            return

        original_url = user.group_name

    tomsk_tz = timezone(timedelta(hours=7))
    now = datetime.now(tomsk_tz)
    current_day = now.isoweekday()

    if original_url.isdigit():
        group_url = build_schedule_url_by_group(original_url)
        group_id = original_url
        week = get_current_study_week()
    else:
    # Пытаемся найти ID группы по имени в БД
        async with AsyncSessionLocal() as session:
            from bot.database.crud import get_group_id_by_name
            found_group_id = await get_group_id_by_name(session, original_url)
            logging.info(f"[Debug] Searching group '{original_url}', found_id: {found_group_id}")

        if found_group_id:
            group_url = build_schedule_url_by_group(found_group_id)
            group_id = found_group_id
            week = get_current_study_week()
        else:
            info = extract_group_info(original_url)
            if not info:
                logging.warning(f"[Schedule] Could not find group or extract info from '{original_url}'")
                await message.answer("❌ Группа не найдена. Попробуйте отправить номер (например, 42624) или полную ссылку.")
                return

            group_id, year, domain = info
            week = get_current_study_week(year)
            group_url = build_schedule_url(group_id, year, week, domain)

    # 1. Проверяем кэш в Redis
    redis_key = f"schedule_cache:{group_id}:{week}:{current_day}"
    try:
        cached_text = await redis_conn.get(redis_key)
        if cached_text:
            logging.info(f"[Redis Cache Hit] Group={group_id}, Week={week}, Day={current_day}")
            await message.answer(cached_text + "\n\n⚡ <i>(из кэша Redis)</i>", parse_mode="HTML", reply_markup=get_main_keyboard())
            return
    except Exception as e:
        logging.error(f"Redis get error: {e}")

    # 2. Проверяем кэш в БД
    async with AsyncSessionLocal() as session:
        from bot.database.crud import get_schedule_by_day, save_week_schedule
        cached_lessons = await get_schedule_by_day(session, original_url, week, current_day)

    if cached_lessons:
        logging.info(f"[DB Cache Hit] Group={group_id}, Week={week}, Day={current_day}")
        schedule_text = format_schedule_from_db(cached_lessons, current_day)
        try:
            await redis_conn.set(redis_key, schedule_text, ex=7200) # Кэш на 2 часа (7200 секунд)
        except Exception as e:
            logging.error(f"Redis set error: {e}")
        await message.answer(schedule_text, parse_mode="HTML", reply_markup=get_main_keyboard())
        return

    # 3. Если нет в кэше — парсим и сохраняем
    logging.info(f"[Cache Miss] Group={group_id}, Week={week}. Parsing...")
    await message.answer(f"⏳ Загружаю расписание за <b>{week}-ю неделю</b> с сайта ТПУ...", parse_mode="HTML")

    try:
        from bot.services.parser import parse_whole_week
        week_data = await parse_whole_week(group_url)

        if week_data:
            async with AsyncSessionLocal() as session:
                await save_week_schedule(session, original_url, week, week_data)

            lessons = week_data.get(current_day, [])
            schedule_text = format_schedule_from_parser(lessons, current_day)
            # Сохраняем в Redis
            await redis_conn.set(redis_key, schedule_text, ex=7200)
        else:
            schedule_text = "❌ Не удалось получить расписание с сайта."

    except Exception as e:
        logging.error(f"[Schedule Error] {e}")
        schedule_text = "❌ Ошибка при загрузке расписания."

    await message.answer(schedule_text, parse_mode="HTML", reply_markup=get_main_keyboard())


def format_schedule_from_db(lessons: list, day_num: int) -> str:
    days_names = {1: "Понедельник", 2: "Вторник", 3: "Среда", 4: "Четверг", 5: "Пятница", 6: "Суббота", 7: "Воскресенье"}
    day_name = days_names.get(day_num, "")

    if not lessons:
        return f"📅 <b>{day_name}:</b>\n\nСегодня пар нет! Отдыхай! 🎉"

    text = f"📅 <b>Расписание ({day_name}, из базы):</b>\n\n"
    for lesson in lessons:
        text += f"⏰ <b>{lesson.time_start}</b>\n"
        text += f"📖 {lesson.subject}\n"
        if lesson.lesson_type:
            text += f"📚 {lesson.lesson_type}\n"
        if lesson.teacher:
            text += f"👤 {lesson.teacher}\n"
        if lesson.room:
            text += f"📍 {lesson.room}\n"
        if lesson.other_info:
            text += f"📝 {lesson.other_info}\n"
        text += "─────────────────────\n"
    return text


def format_schedule_from_parser(lessons: list, day_num: int) -> str:
    days_names = {1: "Понедельник", 2: "Вторник", 3: "Среда", 4: "Четверг", 5: "Пятница", 6: "Суббота", 7: "Воскресенье"}
    day_name = days_names.get(day_num, "")

    if not lessons:
        return f"📅 <b>{day_name}:</b>\n\nСегодня пар нет! Отдыхай! 🎉"

    text = f"📅 <b>Расписание ({day_name}, обновлено):</b>\n\n"
    for lesson in lessons:
        text += f"⏰ <b>{lesson['time']}</b>\n"
        text += f"📖 Предмет: {lesson['subject']}\n"
        if lesson['type']:
            text += f"📚 {lesson['type']}\n"
        if lesson['teacher']:
            text += f"👤 {lesson['teacher']}\n"
        if lesson['room']:
            text += f"📍 Кабинет: {lesson['room']}\n"
        if lesson.get('other'):
            text += f"📝 {lesson['other']}\n"
        text += "─────────────────────\n"
    return text


# === ВВОД ССЫЛКИ ===
@base_router.message(RegistrationState.waiting_for_link)
async def process_link_input(message: Message, state: FSMContext):
    user_input = message.text.strip()

    if user_input == "❌ Отмена":
        await state.clear()
        await message.answer("Окей, без проблем. Выбери действие в меню.", reply_markup=get_main_keyboard())
        return

    async with AsyncSessionLocal() as session:
        from bot.database.crud import get_group_id_by_name
        found_id = await get_group_id_by_name(session, user_input)
        logging.info(f"[Debug] Searching for group: '{user_input}', found_id: {found_id}")

    if "tpu.ru" not in user_input and not user_input.isdigit() and not found_id:
        await message.answer(
            f"❌ Группа '{user_input}' не найдена в базе, и это не ссылка.\n\n"
            "Пришли мне номер своей группы (например: 42624) или название (например: 8К44) или ссылку на расписание с сайта https://rasp.tpu.ru\n\n"
            "Или нажми «❌ Отмена».",
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard()
        )
        return

    async with AsyncSessionLocal() as session:
        await update_user_group(session, message.from_user.id, user_input)

    await state.clear()

    # === АВТОПОДСТАНОВКА НЕДЕЛИ ПРИ ПЕРВОМ ПОКАЗЕ ===
    original_url = user_input

    # Пытаемся найти по ID или имени
    actual_url = None
    if original_url.isdigit():
        actual_url = build_schedule_url_by_group(original_url)
    else:
        async with AsyncSessionLocal() as session:
            from bot.database.crud import get_group_id_by_name
            found_id = await get_group_id_by_name(session, original_url)
            if found_id:
                actual_url = build_schedule_url_by_group(found_id)

        if not actual_url:
            info = extract_group_info(user_input)
            if info:
                group_id, year, domain = info
                week = get_current_study_week(year)
                actual_url = build_schedule_url(group_id, year, week, domain)
                logging.info(f"[AutoWeek] Первый показ: Группа={group_id}, Год={year}, Неделя={week}")

    if actual_url:
        week = get_current_study_week()
        await message.answer(f"📅 Загружаю расписание за <b>{week}-ю неделю</b>...", parse_mode="HTML")
    else:
        actual_url = user_input
        await message.answer("🔄 Ссылка сохранена! Загружаю расписание...")

    tomsk_tz = timezone(timedelta(hours=7))
    current_day = datetime.now(tomsk_tz).isoweekday()

    try:
        schedule_text = await parse_tpu_schedule(actual_url, current_day)
    except Exception as e:
        logging.error(f"[Schedule Error] {e}")
        schedule_text = await parse_tpu_schedule(original_url, current_day)

    await message.answer(schedule_text, parse_mode="HTML", reply_markup=get_main_keyboard())


# === СМЕНИТЬ ССЫЛКУ ===
@base_router.message(F.text.contains("Сменить ссылку расписания"), StateFilter("*"))
async def cmd_change_link(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Отправь новую ссылку на расписание с сайта https://rasp.tpu.ru или номер группы (например: 42624)\n\n"
        "Или нажми «❌ Отмена», чтобы оставить всё как есть.",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(RegistrationState.waiting_for_link)


# === ОТМЕНА ===
@base_router.message(F.text == "❌ Отмена", StateFilter(RegistrationState.waiting_for_link))
async def cancel_link_input(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Окей, отменено. Выбери действие в меню.", reply_markup=get_main_keyboard())