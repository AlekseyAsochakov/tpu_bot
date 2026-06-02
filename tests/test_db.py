import pytest
from unittest.mock import AsyncMock, MagicMock
from bot.database.crud import get_user_by_tg_id
from bot.database.models import User

@pytest.mark.asyncio
async def test_get_user_by_tg_id():
    # Создаем мок сессии
    mock_session = AsyncMock()
    mock_result = MagicMock()

    # Создаем тестового пользователя
    test_user = User(id=1, tg_id=12345, full_name="Test User")

    # Настраиваем мок результата
    mock_result.scalar_one_or_none.return_value = test_user
    mock_session.execute.return_value = mock_result

    # Вызываем функцию
    user = await get_user_by_tg_id(mock_session, 12345)

    assert user is not None
    assert user.tg_id == 12345
    assert user.full_name == "Test User"
    mock_session.execute.assert_called_once()
