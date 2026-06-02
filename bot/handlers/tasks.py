# tasks.py
import json
import logging
import os
from aiogram import Router, F, Bot
from datetime import datetime, timedelta
from sqlalchemy import select
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile, ContentType,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.filters import StateFilter
from icalendar import Calendar, Event

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.config import config
from bot.database.db import AsyncSessionLocal
from bot.database.crud import add_task, get_user_tasks, get_or_create_user
from bot.database.models import Task, User
from bot.handlers.base import get_main_keyboard, get_cancel_keyboard

tasks_router = Router()


class TaskState(StatesGroup):
    waiting_for_ai_input = State()
    waiting_for_subject = State()
    waiting_for_title = State()
    waiting_for_date = State()


# === AI UTILS ===
async def extract_task_info(text: str) -> dict | None:
    """Использует LLM для извлечения данных о задаче из текста."""
    if not config.OPENAI_API_KEY:
        return None

    import openai
    client = openai.AsyncOpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_API_BASE)

    now = datetime.now()
    prompt = (
        f"Сегодняшняя дата: {now.strftime('%d.%m.%Y (%A)')}. "
        "Извлеки информацию о учебном задании из следующего текста. "
        "Верни ТОЛЬКО JSON с полями: subject (предмет), title (что сделать), due_date (дата в формате ДД.ММ.ГГГГ). "
        "Если дата относительная (например, 'в следующую пятницу'), рассчитай её от сегодняшней даты. "
        f"Текст: \"{text}\""
    )

    try:
        response = await client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "Ты — помощник, который извлекает данные в формате JSON."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logging.error(f"AI Extraction error: {e}")
        return None


async def transcribe_voice(bot: Bot, file_id: str) -> str | None:
    """Скачивает голосовое и распознает через Whisper (Groq)."""
    if not config.OPENAI_API_KEY:
        return None

    import openai
    client = openai.AsyncOpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_API_BASE)

    file = await bot.get_file(file_id)
    file_path = f"{file_id}.ogg"
    await bot.download_file(file.file_path, file_path)

    try:
        with open(file_path, "rb") as audio_file:
            transcript = await client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=audio_file,
                language="ru"
            )
        return transcript.text
    except Exception as e:
        logging.error(f"Whisper transcription error: {e}")
        return None
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


@tasks_router.message(F.text == "📅 Экспорт в календарь")
async def cmd_export_calendar(message: Message):
    async with AsyncSessionLocal() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.full_name)
        tasks = await get_user_tasks(session, user.id)

    if not tasks:
        await message.answer("У тебя нет дедлайнов для экспорта.")
        return

    cal = Calendar()
    cal.add('prodid', '-//TPU Bot Calendar//ru//')
    cal.add('version', '2.0')

    for task in tasks:
        if not task.due_date:
            continue
        event = Event()
        event.add('summary', f"Дедлайн: {task.subject} - {task.title}")
        event.add('dtstart', task.due_date)
        event.add('dtend', task.due_date + timedelta(hours=1))
        event.add('description', f"Предмет: {task.subject}\nЗадание: {task.title}")
        cal.add_component(event)

    ics_data = cal.to_ical()
    file_input = BufferedInputFile(ics_data, filename="tpu_deadlines.ics")
    await message.answer_document(file_input, caption="📅 Твой календарь дедлайнов в формате iCal")



def format_time_left(due_date: datetime) -> str:
    """Форматирует оставшееся время до дедлайна."""
    now = datetime.utcnow()
    delta = due_date - now

    if delta.total_seconds() < 0:
        # Просрочено
        overdue = abs(delta)
        days = overdue.days
        hours = overdue.seconds // 3600
        minutes = (overdue.seconds % 3600) // 60
        if days > 0:
            return f"🔴 Просрочено на {days} д {hours} ч"
        elif hours > 0:
            return f"🔴 Просрочено на {hours} ч {minutes} мин"
        else:
            return f"🔴 Просрочено на {minutes} мин"
    else:
        # Осталось
        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        if days > 0:
            return f"⏳ Осталось: {days} д {hours} ч"
        elif hours > 0:
            return f"⏳ Осталось: {hours} ч {minutes} мин"
        else:
            return f"⏳ Осталось: {minutes} мин"


async def cancel_fsm(state: FSMContext, message: Message):
    """Хелпер для отмены FSM и возврата в главное меню."""
    await state.clear()
    await message.answer("Окей, отменено. Выбери действие в меню.", reply_markup=get_main_keyboard())


@tasks_router.message(F.text.contains("Добавить дедлайн"), StateFilter("*"))
async def cmd_add_task(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "✨ <b>Умный ввод дедлайна</b>\n\n"
        "Просто напиши мне текст (например: <i>'Сдать лабу по физике в пятницу'</i>) "
        "или отправь <b>голосовое сообщение</b> 🎙\n\n"
        "Я сам пойму предмет и дату! Или нажми кнопку ниже для ручного ввода.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⌨️ Ручной ввод")],
                [KeyboardButton(text="❌ Отмена")]
            ],
            resize_keyboard=True
        ),
        parse_mode="HTML"
    )
    await state.set_state(TaskState.waiting_for_ai_input)


@tasks_router.message(TaskState.waiting_for_ai_input, F.content_type.in_({ContentType.TEXT, ContentType.VOICE}))
async def process_ai_deadline(message: Message, state: FSMContext, bot: Bot):
    # Если пришел текст, который является кнопкой главного меню - игнорируем здесь,
    # чтобы сработали глобальные хендлеры (т.к. у них StateFilter("*"))
    if message.text and any(keyword in message.text for keyword in ["Расписание на сегодня", "Расписание пар", "Мои дедлайны", "Сменить ссылку расписания"]):
        return

    if message.text == "❌ Отмена":
        await cancel_fsm(state, message)
        return

    if message.text == "⌨️ Ручной ввод":
        await message.answer("По какому предмету дедлайн?", reply_markup=get_cancel_keyboard())
        await state.set_state(TaskState.waiting_for_subject)
        return

    # Обработка голоса или текста
    text = message.text
    if message.voice:
        await message.answer("🎧 Распознаю голос...")
        text = await transcribe_voice(bot, message.voice.file_id)
        if not text:
            await message.answer("❌ Не удалось распознать голос. Попробуй написать текстом.")
            return
        await message.answer(f"📝 Текст: <i>\"{text}\"</i>", parse_mode="HTML")

    if not config.OPENAI_API_KEY:
        await message.answer("⚠️ AI не настроен. Перехожу к ручному вводу. Какой предмет?")
        await state.set_state(TaskState.waiting_for_subject)
        return

    await message.answer("🤖 Анализирую дедлайн...")
    extracted = await extract_task_info(text)

    if not extracted or not all(k in extracted for k in ["subject", "title", "due_date"]):
        await message.answer("❌ Не удалось извлечь данные. Давай введем вручную. Какой предмет?")
        await state.set_state(TaskState.waiting_for_subject)
        return

    try:
        due_date = datetime.strptime(extracted['due_date'], "%d.%m.%Y")
        subject = extracted['subject']
        title = extracted['title']

        async with AsyncSessionLocal() as session:
            user = await get_or_create_user(session, message.from_user.id, message.from_user.full_name)
            await add_task(session, user.id, title, subject, due_date)

        await state.clear()
        await message.answer(
            f"✅ <b>Задача добавлена!</b>\n"
            f"📖 Предмет: {subject}\n"
            f"📝 Задание: {title}\n"
            f"📅 Дедлайн: {due_date.strftime('%d.%m.%Y')}",
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Task save error: {e}")
        await message.answer("❌ Ошибка при сохранении. Попробуй ручной ввод.")
        await state.set_state(TaskState.waiting_for_subject)


@tasks_router.message(TaskState.waiting_for_subject)
async def process_subject(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_fsm(state, message)
        return

    await state.update_data(subject=message.text)
    await message.answer(
        "Что именно нужно сделать? (например: Лаба №3)\n\n"
        "Или нажми «❌ Отмена», чтобы отменить.",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(TaskState.waiting_for_title)


@tasks_router.message(TaskState.waiting_for_title)
async def process_title(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_fsm(state, message)
        return

    await state.update_data(title=message.text)
    await message.answer(
        "Когда нужно сдать? Напиши дату в формате ДД.ММ.ГГГГ (например: 25.05.2024)\n\n"
        "Или нажми «❌ Отмена», чтобы отменить.",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(TaskState.waiting_for_date)


@tasks_router.message(TaskState.waiting_for_date)
async def process_date(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_fsm(state, message)
        return

    data = await state.get_data()
    subject = data['subject']
    title = data['title']

    try:
        due_date = datetime.strptime(message.text, "%d.%m.%Y")
    except ValueError:
        await message.answer(
            "❌ Неверный формат даты! Пожалуйста, используй формат ДД.ММ.ГГГГ\n\n"
            "Или нажми «❌ Отмена», чтобы отменить.",
            reply_markup=get_cancel_keyboard()
        )
        return

    # Проверка: не прошла ли уже дата
    now = datetime.utcnow()
    if due_date < now.replace(hour=0, minute=0, second=0, microsecond=0):
        await message.answer(
            "⚠️ Эта дата уже прошла! Дедлайн должен быть в будущем.\n\n"
            "Введи другую дату или нажми «❌ Отмена».",
            reply_markup=get_cancel_keyboard()
        )
        return

    async with AsyncSessionLocal() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.full_name)
        await add_task(session, user.id, title, subject, due_date)

    await state.clear()
    await message.answer(
        f"✅ Задача добавлена!\n"
        f"📖 Предмет: {subject}\n"
        f"📝 Задание: {title}\n"
        f"📅 Дедлайн: {due_date.strftime('%d.%m.%Y')}",
        reply_markup=get_main_keyboard()
    )


@tasks_router.message(F.text.contains("Мои дедлайны"), StateFilter("*"))
async def cmd_my_tasks(message: Message, state: FSMContext):
    await state.clear()
    async with AsyncSessionLocal() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.full_name)
        tasks = await get_user_tasks(session, user.id)

    if not tasks:
        await message.answer("У тебя пока нет дедлайнов. Отдыхай! ☕", reply_markup=get_main_keyboard())
        return

    now = datetime.utcnow()
    text = "📚 Твои дедлайны:\n\n"

    for i, task in enumerate(tasks, 1):
        status = "✅" if task.is_completed else "🕒"
        date_str = task.due_date.strftime('%d.%m.%Y') if task.due_date else "Без даты"
        time_left = format_time_left(task.due_date) if task.due_date else ""

        if task.due_date and task.due_date < now:
            status = "🔴"
            text += f"{i}. {status} <b>{task.subject}</b>: {task.title}\n"
            text += f"   📅 {date_str} | {time_left}\n\n"
        else:
            text += f"{i}. {status} <b>{task.subject}</b>: {task.title}\n"
            text += f"   📅 {date_str} | {time_left}\n\n"

    text += "<i>Нажми на кнопку ниже, чтобы удалить задачу:</i>"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🗑 Удалить #{i}", callback_data=f"delete_task:{task.id}")]
            for i, task in enumerate(tasks, 1)
        ]
    )

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@tasks_router.callback_query(F.data.startswith("delete_task:"))
async def process_delete_task(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])

    async with AsyncSessionLocal() as session:
        stmt_user = select(User).where(User.tg_id == callback.from_user.id)
        result_user = await session.execute(stmt_user)
        user = result_user.scalar_one_or_none()

        if not user:
            await callback.answer("❌ Пользователь не найден.", show_alert=True)
            return

        stmt_task = select(Task).where(Task.id == task_id)
        result_task = await session.execute(stmt_task)
        task = result_task.scalar_one_or_none()

        if task and task.user_id == user.id:
            await session.delete(task)
            await session.commit()
            await callback.answer("✅ Задача удалена!")
            await callback.message.edit_text(
                "📚 Задача удалена. Нажми «📚 Мои дедлайны», чтобы обновить список."
            )
        else:
            await callback.answer("❌ Не удалось удалить задачу.", show_alert=True)