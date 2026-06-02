import pytest
from bot.services.parser import extract_group_info, get_current_academic_year

def test_extract_group_info_valid():
    url = "https://ro-rasp.tpu.ru/gruppa_42624/2025/1/view.html"
    result = extract_group_info(url)
    assert result == ("42624", 2025, "ro-rasp.tpu.ru")

def test_extract_group_info_invalid():
    url = "https://google.com"
    result = extract_group_info(url)
    assert result is None

def test_get_current_academic_year():
    year = get_current_academic_year()
    assert isinstance(year, int)
    assert year >= 2024
