# Database connection parameters (from requirements)
# По умолчанию — удалённая PostgreSQL (185.43.5.8). BLOCKS и скрипты миграций читают DB_CONFIG.
#
# Чтобы всё шло на удалённую БД с рабочего ПК:
#   • не задавайте MC_DB_LOCAL (иначе host=127.0.0.1, если ниже не переопределено);
#   • при необходимости задайте MC_PG_HOST / MC_PG_DB / MC_PG_USER / MC_PG_PASSWORD —
#     тогда calc.db_postgres возьмёт их в первую очередь.
# На сервере (веб QR) для доступа к Postgres по loopback: MC_DB_LOCAL=1, если MC_PG_HOST не задан.
import os
import sys

_raw = (os.environ.get("MC_PG_HOST") or "").strip()
if _raw:
    _db_host = _raw
elif os.environ.get("MC_DB_LOCAL"):
    _db_host = "127.0.0.1"
else:
    _db_host = "185.43.5.8"

DB_CONFIG = {
    'dbname': 'database',
    'user': 'admin',
    'password': '89522201675pP',
    'host': _db_host,
    'port': '5432',
}

# Прайс: в таблицах стекла/обработки — уже нормализованные ₽ (миграция tools/apply_glass_prices_div_1_2.py).
# MAIN читает как есть; Streamlit — ×1.2 в CALC_WINDOWS/db_price_markup.py.

# База для QR и ссылок WEB_QR: …/remnant (старый вид) или …/qr при едином WEB_SERVICE.
# При WEB_QR_MOUNT_PATH=/qr к origin автоматически добавится /qr, если здесь не указан.
QR_BASE_URL = 'http://185.43.5.8:5000/remnant'

# Label size (mm) - A6
LABEL_WIDTH_MM = 105
LABEL_HEIGHT_MM = 148

# Папка для сохранения файла этикеток (Excel). Пустая строка = папка запуска программы.
LABEL_EXPORT_DIR = ''

# Переопределение настроек с другого ПК: положите config_local.py рядом с exe
if getattr(sys, 'frozen', False):
    _base = os.path.dirname(os.path.abspath(sys.executable))
    _local = os.path.join(_base, 'config_local.py')
    if os.path.isfile(_local):
        try:
            with open(_local, 'r', encoding='utf-8') as f:
                exec(compile(f.read(), _local, 'exec'), globals())
        except Exception:
            pass
