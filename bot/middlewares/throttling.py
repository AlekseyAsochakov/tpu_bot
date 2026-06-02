import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message
from redis.asyncio import Redis


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, redis: Redis, threshold: float = 0.7):
        self.redis = redis
        self.threshold = threshold
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or not event.text:
            return await handler(event, data)

        # Мы ограничиваем только запрос расписания, так как это тяжелая операция
        if event.text != "📅 Расписание на сегодня":
            return await handler(event, data)

        user_id = event.from_user.id
        key = f"throttle_{user_id}"

        last_request_time = await self.redis.get(key)
        now = time.time()

        if last_request_time:
            if now - float(last_request_time) < self.threshold:
                await event.answer("⚠️ Пожалуйста, не спамьте! Подождите пару секунд.")
                return

        await self.redis.set(key, now, ex=max(1, int(self.threshold)))
        return await handler(event, data)
