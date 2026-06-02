# TPU Schedule Assistant Bot 🤖

Продвинутый Telegram-бот для студентов ТПУ с микросервисной архитектурой, кэшированием и AI-функциями.

## 🚀 Основные возможности

*   **📅 Умное расписание:** Получение актуального расписания звонков и занятий.
*   **⚡ Многоуровневое кэширование:** Использование Redis и PostgreSQL для мгновенного ответа под нагрузкой.
*   **🤖 AI-дедлайны:** Добавление учебных задач голосом или текстом (используется Whisper и LLM для парсинга).
*   **🔔 Уведомления:** Автоматические напоминания о дедлайнах за 1 и 3 дня.
*   **📅 Экспорт:** Генерация `.ics` файлов для синхронизации с Google/Apple календарями.
*   **📊 Мониторинг:** Встроенные метрики Prometheus и дашборды Grafana.

## 🛠 Технологический стек

*   **Language:** Python 3.10+
*   **Framework:** Aiogram 3.x (Bot), FastAPI (Webhooks & API)
*   **Database:** PostgreSQL (SQLAlchemy 2.0), Redis
*   **DevOps:** Docker, Docker Compose, Nginx
*   **Monitoring:** Prometheus, Grafana
*   **AI:** OpenAI API (GPT-4o / Llama 3 via Groq), Whisper

## 🏗 Архитектура

Проект построен на микросервисах:
1.  **Bot Service:** Основная логика взаимодействия с пользователем.
2.  **Parser Service:** Изолированный сервис на базе Playwright для парсинга сайта ТПУ.
3.  **Database:** PostgreSQL для долгосрочного хранения.
4.  **Redis:** Для FSM (состояний) и быстрого кэширования.

## 🔧 Установка и запуск

1.  Клонируйте репозиторий:
    ```bash
    git clone https://github.com/AlekseyAsochakov/tpu_bot.git
    cd tpu_bot
    ```

2.  Создайте файл `.env` на основе примера и заполните ключи:
    ```env
    BOT_TOKEN=your_token
    OPENAI_API_KEY=your_key
    ...
    ```

3.  Запустите проект через Docker Compose:
    ```bash
    docker-compose up --build -d
    ```

## 📈 Мониторинг

После запуска метрики доступны по адресу:
*   **Grafana:** `http://localhost:3000`
*   **Prometheus:** `http://localhost:9090`
*   **Parser Metrics:** `http://localhost:8001/metrics`
