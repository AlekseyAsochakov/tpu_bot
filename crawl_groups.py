import asyncio
import re
import logging
from playwright.async_api import async_playwright
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.dialects.postgresql import insert
from bs4 import BeautifulSoup

from bot.database.models import GroupMapping, Base
from bot.config import config

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def crawl():
    engine = create_async_engine(config.database_url_async, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_playwright() as p:
        logger.info("Запуск браузера...")
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        base_url = "https://ro-rasp.tpu.ru"
        logger.info(f"Загружаю {base_url}...")
        try:
            await page.goto(base_url, timeout=60000)
        except Exception as e:
            logger.error(f"Не удалось загрузить главную страницу: {e}")
            await browser.close()
            return

        await page.wait_for_selector('a[href*="department.html?id="]')
        links = await page.eval_on_selector_all('a[href*="department.html?id="]', 'elements => elements.map(e => e.href)')

        department_links = list(set(links))
        logger.info(f"Найдено подразделений: {len(department_links)}")

        async with async_session() as session:
            for dep_url in department_links:
                logger.info(f"Сканирую подразделение: {dep_url}")
                try:
                    await page.goto(dep_url, timeout=60000)
                    await page.wait_for_load_state("networkidle")

                    # Находим все вкладки курсов
                    course_tabs = await page.query_selector_all('ul.nav-tabs li a')

                    if not course_tabs:
                        # Если вкладок нет, просто сканируем текущую страницу
                        content = await page.content()
                        await process_page_groups(content, session)
                    else:
                        for i in range(len(course_tabs)):
                            try:
                                # Заново ищем вкладки, так как DOM мог измениться
                                tabs = await page.query_selector_all('ul.nav-tabs li a')
                                if i < len(tabs):
                                    tab_text = await tabs[i].inner_text()
                                    logger.info(f"  Переключаюсь на вкладку: {tab_text.strip()}")
                                    await tabs[i].click()
                                    await page.wait_for_timeout(2000) # Ждем загрузки
                                    content = await page.content()
                                    await process_page_groups(content, session)
                            except Exception as tab_e:
                                logger.error(f"  Ошибка при клике на вкладку {i}: {tab_e}")

                    # Коммитим после каждого подразделения, чтобы не потерять данные при сбое
                    await session.commit()
                except Exception as e:
                    logger.error(f"Ошибка при сканировании {dep_url}: {e}")

        await browser.close()
    logger.info("Сбор завершен!")

async def process_page_groups(html, session):
    soup = BeautifulSoup(html, 'html.parser')
    count = 0
    for a in soup.find_all('a', href=True):
        if "gruppa_" in a['href']:
            group_name = a.text.strip()
            if not group_name:
                continue

            match = re.search(r'gruppa_(\d+)', a['href'])
            if match:
                group_id = match.group(1)

                # Robust PostgreSQL Upsert using SQLAlchemy
                stmt = insert(GroupMapping).values(
                    group_name=group_name,
                    group_id=group_id
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=['group_name'],
                    set_=dict(group_id=group_id)
                )
                await session.execute(stmt)
                count += 1

    if count > 0:
        logger.info(f"    Найдено групп на странице: {count}")

if __name__ == "__main__":
    asyncio.run(crawl())
