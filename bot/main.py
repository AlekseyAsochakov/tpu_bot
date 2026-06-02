import asyncio
import logging
import pydantic
from datetime import datetime, timedelta

import fastapi
from aiogram import Bot, Dispatcher, types
from aiogram.utils.web_app import safe_parse_webapp_init_data
from aiogram.fsm.storage.redis import RedisStorage
from fastapi import FastAPI, Header, HTTPException
from redis.asyncio import Redis
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.config import config
from bot.database.db import engine, AsyncSessionLocal
from bot.database.models import Base
from bot.database.crud import (
    get_all_active_tasks, mark_task_notified, get_all_unique_group_urls,
    save_week_schedule, get_user_by_tg_id, get_user_tasks, toggle_task_complete,
    add_task, update_task_date, delete_task
)
from bot.handlers.base import base_router
from bot.handlers.tasks import tasks_router
from bot.middlewares.throttling import ThrottlingMiddleware
from bot.services.parser import extract_group_info, get_current_study_week, build_schedule_url, parse_whole_week
from bot.services.metrics import USER_INTERACTION_COUNT
from prometheus_client import make_asgi_app

logging.basicConfig(level=logging.INFO)

# Setup Redis
redis_conn = Redis(host=config.REDIS_HOST, port=config.REDIS_PORT, decode_responses=True)
storage = RedisStorage(redis=redis_conn)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=storage)

# Setup FastAPI (for Webhooks and Metrics)
app = FastAPI()

# Mount static files
# app.mount("/static", StaticFiles(directory="static"), name="static")

def get_tg_user_id(x_tg_init_data: str = Header(None)):
    if not x_tg_init_data:
        raise HTTPException(status_code=401, detail="Missing initData")
    try:
        # Important: BOT_TOKEN should not have leading/trailing spaces
        token = config.BOT_TOKEN.strip()
        data = safe_parse_webapp_init_data(token=token, init_data=x_tg_init_data)
        return data.user.id
    except ValueError as e:
        logging.error(f"WebApp auth failed: {e}. initData: {x_tg_init_data[:50]}...")
        raise HTTPException(status_code=401, detail=f"Invalid initData: {e}")

@app.get("/api/deadlines")
async def api_get_deadlines(tg_id: int = fastapi.Depends(get_tg_user_id)):
    async with AsyncSessionLocal() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            return []
        tasks = await get_user_tasks(session, user.id)
        return [
            {
                "id": t.id,
                "title": t.title,
                "subject": t.subject,
                "due_date": t.due_date.isoformat() if t.due_date else None,
                "is_completed": t.is_completed
            } for t in tasks
        ]

@app.post("/api/deadlines/{task_id}/toggle")
async def api_toggle_deadline(task_id: int, tg_id: int = fastapi.Depends(get_tg_user_id)):
    async with AsyncSessionLocal() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Verify task belongs to user
        from sqlalchemy import select
        from bot.database.models import Task
        stmt = select(Task).where(Task.id == task_id, Task.user_id == user.id)
        res = await session.execute(stmt)
        task = res.scalar_one_or_none()

        if not task:
            raise HTTPException(status_code=403, detail="Access denied")

        await toggle_task_complete(session, task_id)
        return {"status": "ok"}

class TaskCreate(pydantic.BaseModel):
    title: str
    subject: str
    due_date: str # ISO format

@app.post("/api/deadlines")
async def api_create_deadline(data: TaskCreate, tg_id: int = fastapi.Depends(get_tg_user_id)):
    async with AsyncSessionLocal() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        try:
            due_date = datetime.fromisoformat(data.due_date.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format")

        await add_task(session, user.id, data.title, data.subject, due_date)
        return {"status": "ok"}

class TaskUpdateDate(pydantic.BaseModel):
    due_date: str

@app.post("/api/deadlines/{task_id}/update_date")
async def api_update_deadline_date(task_id: int, data: TaskUpdateDate, tg_id: int = fastapi.Depends(get_tg_user_id)):
    async with AsyncSessionLocal() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        from sqlalchemy import select
        from bot.database.models import Task
        stmt = select(Task).where(Task.id == task_id, Task.user_id == user.id)
        res = await session.execute(stmt)
        if not res.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="Access denied")

        try:
            due_date = datetime.fromisoformat(data.due_date.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format")

        await update_task_date(session, task_id, due_date)
        return {"status": "ok"}

@app.delete("/api/deadlines/{task_id}")
async def api_delete_deadline(task_id: int, tg_id: int = fastapi.Depends(get_tg_user_id)):
    async with AsyncSessionLocal() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        from sqlalchemy import select
        from bot.database.models import Task
        stmt = select(Task).where(Task.id == task_id, Task.user_id == user.id)
        res = await session.execute(stmt)
        if not res.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="Access denied")

        await delete_task(session, task_id)
        return {"status": "ok"}

# Add prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def check_deadlines(bot: Bot):
    """Фоновая задача: проверяет дедлайны каждый час и шлёт уведомления."""
    while True:
        try:
            now = datetime.utcnow()
            async with AsyncSessionLocal() as session:
                tasks = await get_all_active_tasks(session)
                for task in tasks:
                    if not task.due_date: continue
                    time_left = task.due_date - now
                    hours_left = time_left.total_seconds() / 3600
                    if 23 <= hours_left <= 25 and not task.notified_1d:
                        try:
                            await bot.send_message(task.user_id, f"⏰ <b>Дедлайн через 1 день!</b>\n\n📖 {task.subject}\n📝 {task.title}", parse_mode="HTML")
                            await mark_task_notified(session, task.id, 1)
                        except Exception: pass
                    elif 71 <= hours_left <= 73 and not task.notified_3d:
                        try:
                            await bot.send_message(task.user_id, f"📢 <b>Дедлайн через 3 дня!</b>\n\n📖 {task.subject}\n📝 {task.title}", parse_mode="HTML")
                            await mark_task_notified(session, task.id, 3)
                        except Exception: pass
        except Exception as e:
            logging.error(f"Deadline checker error: {e}")
        await asyncio.sleep(3600)

async def update_all_schedules():
    logging.info("[Scheduler] Starting global schedule update...")
    async with AsyncSessionLocal() as session:
        urls = await get_all_unique_group_urls(session)

    if not urls:
        logging.info("[Scheduler] No groups found in database.")
        return

    for url in urls:
        try:
            info = extract_group_info(url)
            if not info:
                logging.warning(f"[Scheduler] Could not extract info from URL: {url}")
                continue

            group_id, year, domain = info
            current_week = get_current_study_week(year)

            # Обновляем текущую и следующую недели
            for w in [current_week, current_week + 1]:
                logging.info(f"[Scheduler] Updating Group={group_id}, Week={w}...")
                target_url = build_schedule_url(group_id, year, w, domain)
                week_data = await parse_whole_week(target_url)

                if week_data:
                    async with AsyncSessionLocal() as session:
                        await save_week_schedule(session, url, w, week_data)

                    # Очищаем кэш Redis для этой группы и недели, чтобы данные обновились
                    for day_num in range(1, 8):
                        redis_key = f"schedule_cache:{group_id}:{w}:{day_num}"
                        await redis_conn.delete(redis_key)

                    logging.info(f"[Scheduler] Success for Group={group_id}, Week={w}")
                else:
                    logging.warning(f"[Scheduler] No data for Group={group_id}, Week={w}")

        except Exception as e:
            logging.error(f"[Scheduler] Error updating URL {url}: {e}")

    logging.info("[Scheduler] Global schedule update finished.")

async def start_background_tasks(bot: Bot):
    asyncio.create_task(check_deadlines(bot))
    scheduler = AsyncIOScheduler(timezone="Asia/Tomsk")
    scheduler.add_job(update_all_schedules, 'cron', hour=3, minute=0)
    scheduler.start()

bot_setup_done = False

async def setup_bot():
    global bot_setup_done
    if bot_setup_done:
        return
    await init_db()

    # Register middlewares
    dp.message.middleware(ThrottlingMiddleware(redis=redis_conn))

    dp.include_router(base_router)
    dp.include_router(tasks_router)
    await start_background_tasks(bot)
    bot_setup_done = True

@app.on_event("startup")
async def on_startup():
    await setup_bot()

    if config.WEBHOOK_HOST and config.WEBHOOK_HOST != "polling":
        webhook_url = f"{config.WEBHOOK_HOST}{config.WEBHOOK_PATH}"
        logging.info(f"Setting webhook to: {webhook_url}")
        try:
            await bot.set_webhook(
                url=webhook_url,
                drop_pending_updates=True,
                allowed_updates=dp.resolve_used_update_types()
            )
        except Exception as e:
            logging.error(f"Failed to set webhook: {e}. Falling back to polling mode or continuing startup...")
            # If webhook fails, we don't necessarily want to crash the whole app if it's running FastAPI
            # but for a bot, it's critical.
            # However, if WEBHOOK_HOST is invalid, we might want to warn the user.

@app.post(config.WEBHOOK_PATH)
async def bot_webhook(update: dict):
    telegram_update = types.Update(**update)
    await dp.feed_update(bot=bot, update=telegram_update)

@app.on_event("shutdown")
async def on_shutdown():
    await bot.delete_webhook()
    await redis_conn.close()

async def run_polling():
    logging.info("Starting in POLLING mode...")
    await setup_bot()
    await bot.delete_webhook(drop_pending_updates=True)

    # Start web server for metrics in background when in polling mode
    import uvicorn
    config_uvicorn = uvicorn.Config(app, host=config.WEBAPP_HOST, port=config.WEBAPP_PORT, log_level="info")
    server = uvicorn.Server(config_uvicorn)

    # We use a Task to run the server alongside the dispatcher
    server_task = asyncio.create_task(server.serve())

    try:
        await dp.start_polling(bot)
    finally:
        server.should_exit = True
        await server_task

if __name__ == "__main__":
    if not config.WEBHOOK_HOST or config.WEBHOOK_HOST == "polling":
        try:
            asyncio.run(run_polling())
        except (KeyboardInterrupt, SystemExit):
            logging.info("Bot stopped.")
    else:
        import uvicorn
        uvicorn.run(app, host=config.WEBAPP_HOST, port=config.WEBAPP_PORT)
