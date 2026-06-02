# crud.py
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from bot.database.models import User, Task, Schedule, GroupMapping

async def get_or_create_user(session: AsyncSession, tg_id: int, full_name: str) -> User:
    stmt = select(User).where(User.tg_id == tg_id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        user = User(tg_id=tg_id, full_name=full_name)
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user

async def update_user_group(session: AsyncSession, tg_id: int, group_name: str) -> User:
    stmt = select(User).where(User.tg_id == tg_id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if user:
        user.group_name = group_name
        await session.commit()
        await session.refresh(user)
    return user

async def add_task(session: AsyncSession, user_id: int, title: str, subject: str, due_date: datetime = None) -> Task:
    task = Task(user_id=user_id, title=title, subject=subject, due_date=due_date)
    session.add(task)
    await session.commit()
    return task

async def get_user_tasks(session: AsyncSession, user_id: int) -> list[Task]:
    stmt = select(Task).where(Task.user_id == user_id).order_by(Task.due_date)
    result = await session.execute(stmt)
    return list(result.scalars().all())

async def get_user_by_tg_id(session: AsyncSession, tg_id: int) -> User | None:
    stmt = select(User).where(User.tg_id == tg_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

async def toggle_task_complete(session: AsyncSession, task_id: int) -> bool:
    stmt = select(Task).where(Task.id == task_id)
    result = await session.execute(stmt)
    task = result.scalar_one_or_none()
    if task:
        task.is_completed = not task.is_completed
        await session.commit()
        return True
    return False

async def update_task_date(session: AsyncSession, task_id: int, new_date: datetime) -> bool:
    stmt = select(Task).where(Task.id == task_id)
    result = await session.execute(stmt)
    task = result.scalar_one_or_none()
    if task:
        task.due_date = new_date
        await session.commit()
        return True
    return False

async def delete_task(session: AsyncSession, task_id: int) -> bool:
    from sqlalchemy import delete
    stmt = delete(Task).where(Task.id == task_id)
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount > 0

async def get_all_active_tasks(session: AsyncSession) -> list[Task]:
    now = datetime.utcnow()
    stmt = select(Task).where(
        Task.is_completed == False,
        Task.due_date != None,
        Task.due_date > now
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())

async def mark_task_notified(session: AsyncSession, task_id: int, days: int):
    stmt = select(Task).where(Task.id == task_id)
    result = await session.execute(stmt)
    task = result.scalar_one_or_none()
    if task:
        if days == 1:
            task.notified_1d = True
        elif days == 3:
            task.notified_3d = True
        await session.commit()

async def get_schedule_by_day(session: AsyncSession, group_name: str, week: int, day_of_week: int) -> list[Schedule]:
    stmt = (
        select(Schedule)
        .where(
            Schedule.group_name == group_name,
            Schedule.week == week,
            Schedule.day_of_week == day_of_week
        )
        .order_by(Schedule.pair_number)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())

async def save_week_schedule(session: AsyncSession, group_name: str, week: int, week_data: dict[int, list[dict]]):
    """Сохраняет расписание на всю неделю в БД, предварительно удаляя старое."""
    from sqlalchemy import delete
    # Удаляем старое расписание для этой группы и недели
    delete_stmt = delete(Schedule).where(Schedule.group_name == group_name, Schedule.week == week)
    await session.execute(delete_stmt)

    for day_num, lessons in week_data.items():
        for lesson in lessons:
            new_entry = Schedule(
                group_name=group_name,
                day_of_week=day_num,
                week=week,
                pair_number=lesson.get("pair_number", 0),
                time_start=lesson["time"],
                subject=lesson["subject"],
                lesson_type=lesson.get("type"),
                teacher=lesson.get("teacher"),
                room=lesson.get("room"),
                other_info=lesson.get("other")
            )
            session.add(new_entry)

    await session.commit()

async def get_all_unique_group_urls(session: AsyncSession) -> list[str]:
    stmt = select(User.group_name).where(User.group_name != None).distinct()
    result = await session.execute(stmt)
    return list(result.scalars().all())

async def get_group_id_by_name(session: AsyncSession, group_name: str) -> str | None:
    from sqlalchemy import func
    # Ищем с игнорированием регистра и лишних пробелов
    stmt = select(GroupMapping.group_id).where(
        func.lower(GroupMapping.group_name) == group_name.strip().lower()
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
