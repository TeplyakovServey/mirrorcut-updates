# -*- coding: utf-8 -*-
"""Парсер цены с сайта МДМ (https://www.mdm-complect.ru/catalog/...). Работает только для этого домена."""
import re
from urllib.parse import urlparse


def fetch_price_from_mdm_url(url, timeout=10):
    """
    Загрузить страницу по URL и извлечь цену «для физических лиц» (руб.).

    - Работает ТОЛЬКО для домена www.mdm-complect.ru (или mdm-complect.ru).
    - Использует разметку div.price-main, как в примере пользователя.
    - Возвращает float или None при ошибке/отсутствии цены.
    """
    url = (url or "").strip()
    if not url.startswith("http"):
        return None

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if "mdm-complect.ru" not in host:
        # Для других сайтов ничего не делаем
        return None

    try:
        import requests
        from bs4 import BeautifulSoup  # type: ignore[import-not-found]
    except ImportError:
        # Если нет зависимостей, лучше вернуть None и не падать
        return None

    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
    except Exception:
        return None

    try:
        soup = BeautifulSoup(r.text, "lxml")
        price_block = soup.find("div", class_="price-main")
        if not price_block:
            return None
        # Берём первый токен, очищаем и приводим к float
        raw = (price_block.text or "").strip().split()[0]
        raw = raw.replace("\xa0", "").replace(" ", "").replace(",", ".")
        return float(raw)
    except Exception:
        # На всякий случай оставим старый резервный вариант по regex
        html = r.text
        m = re.search(r"(\d+[.,]\d+)\s*руб", html, re.IGNORECASE)
        if not m:
            return None
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return None

