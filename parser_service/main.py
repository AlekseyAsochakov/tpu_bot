import logging
import re
from fastapi import FastAPI, HTTPException, Query
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from prometheus_client import Counter, Histogram, make_asgi_app

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="TPU Schedule Parser API")

# Prometheus Metrics
REQUEST_COUNT = Counter("parser_requests_total", "Total number of parsing requests", ["status"])
REQUEST_LATENCY = Histogram("parser_request_duration_seconds", "Latency of parsing requests")

# Add prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

def get_time_from_cell(time_cell) -> str:
    title = time_cell.get('title', '').strip()
    if title and re.search(r'\d{1,2}:\d{2}', title):
        return title
    raw = time_cell.get_text(separator=' ')
    return re.sub(r'\s+', ' ', raw).strip()

def extract_cell_text(cell):
    if cell is None:
        return ""
    soup_copy = BeautifulSoup(str(cell), 'html.parser')
    cell_copy = soup_copy.find()
    for hr in cell_copy.find_all('hr'):
        hr.replace_with('\n---HR---\n')
    for br in cell_copy.find_all('br'):
        br.replace_with('\n')
    text = cell_copy.get_text(separator=' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\s+([,.])', r'\1', text)
    text = re.sub(r'\(\s+', '(', text)
    text = re.sub(r'\s+\)', ')', text)
    text = text.replace('\n ---HR--- \n', '\n---HR---\n')
    text = re.sub(r'\n\s*\n+', '\n', text)
    return text.strip()

async def get_html_with_js(url: str) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        await page.route("**/*.{png,jpg,jpeg,svg,gif}", lambda route: route.abort())
        await page.goto(url, wait_until="networkidle", timeout=15000)
        html = await page.content()
        await browser.close()
        return html

@app.get("/parse")
async def parse_schedule(url: str = Query(..., description="The TPU schedule URL to parse")):
    with REQUEST_LATENCY.time():
        try:
            logger.info(f"Parsing URL: {url}")
            html = await get_html_with_js(url)
            soup = BeautifulSoup(html, 'html.parser')
            rows = soup.find_all('tr')

            if not rows:
                REQUEST_COUNT.labels(status="error").inc()
                raise HTTPException(status_code=404, detail="No schedule rows found")

            days_mapping = {
                "понедельник": 1, "вторник": 2, "среда": 3,
                "четверг": 4, "пятница": 5, "суббота": 6
            }

            col_to_day = {}
            header_row = None
            for i, row in enumerate(rows):
                cells = row.find_all(['th', 'td'])
                for col_idx, cell in enumerate(cells):
                    cell_text = cell.get_text().strip().lower()
                    for day_name, day_num in days_mapping.items():
                        if day_name in cell_text:
                            col_to_day[col_idx] = day_num
                if col_to_day:
                    header_row = i
                    break

            if not col_to_day:
                REQUEST_COUNT.labels(status="error").inc()
                return {}

            week_schedule = {i: [] for i in range(1, 7)}
            for row in rows[header_row + 1:]:
                cells = row.find_all(['td', 'th'])
                if not cells: continue
                time_clean = get_time_from_cell(cells[0])
                if not re.search(r'\d{1,2}:\d{2}', time_clean): continue
                pair_number = 0
                time_match = re.search(r'(\d+)', cells[0].get_text())
                if time_match: pair_number = int(time_match.group(1))

                for col_idx, day_num in col_to_day.items():
                    if col_idx >= len(cells): continue
                    day_cell = cells[col_idx]
                    day_text = extract_cell_text(day_cell)
                    if not day_text or len(day_text) < 2: continue

                    lesson_blocks = day_text.split('---HR---')
                    for block in lesson_blocks:
                        lines = [line.strip() for line in block.split('\n') if line.strip()]
                        if not lines: continue
                        subject = None
                        lesson_type = None
                        teacher = None
                        cabinet = None
                        other = []
                        for line in lines:
                            line_lower = line.lower()
                            if re.search(r'[А-ЯЁ][а-яё\-]+\s+[А-ЯЁ]\.(?:\s*[А-ЯЁ]\.)?', line):
                                teacher = line
                                continue
                            if "ауд" in line_lower or re.search(r'к\.\s*[А-ЯЁа-яёA-Za-z0-9]', line):
                                cabinet = re.sub(r'\bк\.', 'корпус', line, flags=re.IGNORECASE).strip(' ,()')
                                continue
                            type_match = re.search(r'\(\s*(ЛБ|ПР|ЛК|ЛАБ|ЛЕК|СЕМ|КС|ЗЧ|ДФ|Э|КТ|СМ|КР|КП|ВБ|ЛЕКЦИЯ|ПРАКТИКА)\s*\)', line, re.IGNORECASE)
                            if type_match:
                                lesson_type = type_match.group(0).upper()
                                remaining = line.replace(type_match.group(0), '').strip().strip(',.- ')
                                if remaining:
                                    if not subject: subject = remaining
                                    else: other.append(remaining)
                                continue
                            if not subject: subject = line
                            else: other.append(line)

                        if subject or lesson_type:
                            week_schedule[day_num].append({
                                "pair_number": pair_number,
                                "time": time_clean,
                                "subject": subject or "Не указано",
                                "type": lesson_type or "",
                                "teacher": teacher or "",
                                "room": cabinet or "",
                                "other": "\n".join(other)
                            })

            REQUEST_COUNT.labels(status="success").inc()
            return week_schedule
        except Exception as e:
            logger.error(f"Error parsing: {e}")
            REQUEST_COUNT.labels(status="error").inc()
            raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
