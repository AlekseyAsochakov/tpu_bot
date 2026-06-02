FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install playwright browsers and system dependencies
RUN playwright install chromium && playwright install-deps chromium

COPY . .

CMD ["python", "-m", "bot.main"]
