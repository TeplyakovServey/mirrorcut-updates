from collections import defaultdict

from .connection import get_connection

# Table names we create (with mirror_ prefix)
# mirror_cut_archive и mirror_cut_archive_detail — служебные, в редакторе таблиц не показываем
TABLES = [
    'mirror_full_sheets',
    'mirror_remnants',
    'mirror_deleted_remnants',
    'mirror_deleted_full_sheets',
    'mirror_business_waste_threshold',
    'mirror_clients',
    'mirror_client_entities',
    'mirror_suppliers',
    'mirror_quick_clients',
    'mirror_orders',
    'mirror_order_items',
    'mirror_remnant_history',
    'mirror_cut_results',
    'mirror_label_counter',
    'mirror_k_counter',
    'mirror_cut_archive',
    'mirror_cut_archive_detail',
    'mirror_layout_training',
    'mirror_sales_orders',
    'mirror_sales_items',
    'mirror_sales_profile_usage',
    'mirror_quick_estimates',
    'mirror_production_events',
    'mirror_inventory_campaigns',
    'mirror_inventory_type_completion',
    'mirror_inventory_scans',
    'mirror_inventory_losses',
    'mirror_generated_qr_log',
    'mirror_cut_batches',
    'mirror_cut_batch_sheets',
    'mirror_cut_batch_pieces',
    'mirror_profile_cut_events',
    'mirror_desktop_app_release',
]

# Материалы с порогом минимального отхода 250×250 мм (деталь меньше — отход)
DEFAULT_MATERIALS_250x250 = [
    "Дарк Грей",
    "Зеркало Морена",
    "Зеркало серебро",
    "Комфорт Bronze",
    "Комфорт Grey",
    "Лакобель 1013",
    "Лакобель 1015",
    "Лакобель 1236",
    "Лакобель 9003",
    "Лакобель 9005",
    "Лакобель 9010",
    "Лакомат",
    "Мателак бронза",
    "Мателак графит",
    "Мателак серебро",
    "Мателак серебро Crystalvision",
    "Стекло Crystalvision",
    "Стекло бронза",
    "Стекло графит",
    "Стекло матовое (сатинат) Crystalvision",
    "Стекло матовое (сатинат) б\\цв",
    "Стекло матовое (сатинат) бронза",
    "Стекло матовое (сатинат) графит",
    "Стекло Мору",
    "Стекло Мору бронза",
    "Стекло Мору графит",
    "Стекло прозрачное б\\цв",
    "Стекло Стопсол бронза",
    "Стекло Эстриадо",
]


def _norm_supplier_name(name):
    return " ".join((name or "").strip().split()).lower()


def _backfill_suppliers_from_sheets(cur):
    """Создать поставщиков из уникальных имён в mirror_full_sheets.supplier и проставить supplier_id."""
    try:
        cur.execute(
            """
            SELECT DISTINCT TRIM(supplier) AS nm
            FROM mirror_full_sheets
            WHERE supplier IS NOT NULL AND TRIM(supplier) <> ''
            """
        )
        names = [r["nm"] for r in (cur.fetchall() or []) if r.get("nm")]
    except Exception:
        return
    if not names:
        return
    cur.execute("SELECT id, name FROM mirror_suppliers")
    existing = {_norm_supplier_name(r["name"]): int(r["id"]) for r in (cur.fetchall() or [])}
    for nm in names:
        key = _norm_supplier_name(nm)
        if not key or key in existing:
            continue
        cur.execute(
            """
            INSERT INTO mirror_suppliers (supplier_type, name, phone, email, legal_address, actual_address)
            VALUES ('legal', %s, '', '', '', '') RETURNING id
            """,
            (nm.strip(),),
        )
        row = cur.fetchone()
        if row:
            existing[key] = int(row["id"])
    for nm in names:
        key = _norm_supplier_name(nm)
        sid = existing.get(key)
        if not sid:
            continue
        cur.execute(
            """
            UPDATE mirror_full_sheets
            SET supplier_id = %s
            WHERE supplier_id IS NULL AND LOWER(TRIM(supplier)) = %s
            """,
            (sid, key),
        )


UNKNOWN_SUPPLIER_NAME = "Неопознанный"


def _seed_unknown_supplier(cur):
    """Один системный поставщик для неопознанных поставок."""
    try:
        cur.execute(
            "SELECT id FROM mirror_suppliers WHERE LOWER(TRIM(name)) = LOWER(%s) LIMIT 1",
            (UNKNOWN_SUPPLIER_NAME,),
        )
        if cur.fetchone():
            return
        cur.execute(
            """
            INSERT INTO mirror_suppliers (supplier_type, name, phone, email, legal_address, actual_address, notes)
            VALUES ('legal', %s, '', '', '', '', 'Системный поставщик для неопознанных поставок')
            """,
            (UNKNOWN_SUPPLIER_NAME,),
        )
    except Exception:
        pass


def _seed_materials_thresholds(cur):
    """Добавить порог 250×250 мм для каждого материала из списка (если записи нет — вставка, иначе обновление).

    Один round-trip вместо N отдельных INSERT: при удалённом PostgreSQL цикл давал ~40–80 ms × число
    материалов при каждом ensure_tables() после логина.
    """
    if not DEFAULT_MATERIALS_250x250:
        return
    cur.execute(
        """
        INSERT INTO mirror_business_waste_threshold (name, thickness_mm, min_height_mm, min_width_mm)
        SELECT u, 4, 250, 250 FROM unnest(%s::text[]) AS u
        ON CONFLICT (name, thickness_mm) DO UPDATE SET min_height_mm = 250, min_width_mm = 250
        """,
        (list(DEFAULT_MATERIALS_250x250),),
    )


def _migrate_checked_qr_status(cur):
    """Статус checked_qr снят с UI — переносим в made."""
    try:
        cur.execute("UPDATE mirror_orders SET status = 'made' WHERE status = 'checked_qr'")
    except Exception:
        pass
    try:
        cur.execute(
            """
            UPDATE mirror_orders
            SET blocks_calc_json = replace(blocks_calc_json::text, '"checked_qr"', '"made"')::jsonb
            WHERE blocks_calc_json IS NOT NULL
              AND blocks_calc_json::text LIKE '%checked_qr%'
            """
        )
    except Exception:
        pass


def _remove_sergey_space(cur):
    """Удалить материал SERGEY SPACE из всех таблиц (пороги, целые листы, остатки)."""
    for table, has_name in [
        ('mirror_business_waste_threshold', True),
        ('mirror_full_sheets', True),
        ('mirror_remnants', True),
    ]:
        try:
            cur.execute("DELETE FROM %s WHERE name = %%s" % table, ('SERGEY SPACE',))
        except Exception:
            pass


def _refresh_columns_for_table(cur, colmap, table):
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    )
    colmap[table] = {row["column_name"] for row in cur.fetchall()}


def ensure_tables():
    """Create tables if they do not exist (check by name first)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = ANY(%s)
                """,
                (list(TABLES),),
            )
            existing = {row["table_name"] for row in cur.fetchall()}
            colmap = defaultdict(set)
            if existing:
                cur.execute(
                    """
                    SELECT table_name, column_name FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = ANY(%s)
                    """,
                    (list(existing),),
                )
                for row in cur.fetchall():
                    colmap[row["table_name"]].add(row["column_name"])

            def has_col(tbl, c):
                return c in colmap.get(tbl, ())

            def note_col(tbl, c):
                colmap[tbl].add(c)

            if 'mirror_full_sheets' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_full_sheets (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        height_mm INTEGER NOT NULL,
                        width_mm INTEGER NOT NULL,
                        thickness_mm INTEGER NOT NULL DEFAULT 4,
                        arrival_date DATE,
                        supplier VARCHAR(255),
                        cost NUMERIC(12,2) DEFAULT 0,
                        warehouse_number VARCHAR(128),
                        quantity INTEGER NOT NULL DEFAULT 1,
                        comment TEXT
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_full_sheets")
            else:
                for col, typ in [
                    ('arrival_date', 'DATE'),
                    ('supplier', 'VARCHAR(255)'),
                    ('cost', 'NUMERIC(12,2)'),
                    ('warehouse_number', 'VARCHAR(128)'),
                    ('quantity', 'INTEGER'),
                    ('comment', 'TEXT'),
                    ('thickness_mm', 'INTEGER'),
                ]:
                    if not has_col("mirror_full_sheets", col):
                        if col == 'quantity':
                            cur.execute("ALTER TABLE mirror_full_sheets ADD COLUMN quantity INTEGER NOT NULL DEFAULT 1")
                        elif col == 'thickness_mm':
                            cur.execute("ALTER TABLE mirror_full_sheets ADD COLUMN thickness_mm INTEGER NOT NULL DEFAULT 4")
                        else:
                            cur.execute("ALTER TABLE mirror_full_sheets ADD COLUMN %s %s" % (col, typ))
                        note_col("mirror_full_sheets", col)
            if 'mirror_remnants' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_remnants (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        height_mm INTEGER NOT NULL,
                        width_mm INTEGER NOT NULL,
                        thickness_mm INTEGER NOT NULL DEFAULT 4,
                        unique_number VARCHAR(64) UNIQUE NOT NULL,
                        qr_url VARCHAR(512),
                        label_number INTEGER,
                        reserved_for_cut_order_id INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_remnants")
            if 'mirror_business_waste_threshold' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_business_waste_threshold (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        thickness_mm INTEGER NOT NULL DEFAULT 4,
                        min_height_mm INTEGER NOT NULL,
                        min_width_mm INTEGER NOT NULL,
                        UNIQUE(name, thickness_mm)
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_business_waste_threshold")
            else:
                if not has_col("mirror_business_waste_threshold", "thickness_mm"):
                    cur.execute("ALTER TABLE mirror_business_waste_threshold ADD COLUMN thickness_mm INTEGER NOT NULL DEFAULT 4")
                    cur.execute("ALTER TABLE mirror_business_waste_threshold DROP CONSTRAINT IF EXISTS mirror_business_waste_threshold_name_key")
                    cur.execute("ALTER TABLE mirror_business_waste_threshold ADD CONSTRAINT mirror_business_waste_threshold_name_thickness_key UNIQUE (name, thickness_mm)")
                    note_col("mirror_business_waste_threshold", "thickness_mm")
            if 'mirror_suppliers' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_suppliers (
                        id SERIAL PRIMARY KEY,
                        supplier_type VARCHAR(20) NOT NULL DEFAULT 'legal',
                        name VARCHAR(255) NOT NULL,
                        inn VARCHAR(12),
                        kpp VARCHAR(9),
                        okpo VARCHAR(8),
                        ogrn VARCHAR(13),
                        registration TEXT,
                        first_name VARCHAR(255),
                        last_name VARCHAR(255),
                        passport_series VARCHAR(20),
                        passport_number VARCHAR(20),
                        birth_date DATE,
                        gender VARCHAR(20),
                        phone VARCHAR(128) NOT NULL DEFAULT '',
                        email VARCHAR(255) NOT NULL DEFAULT '',
                        legal_address TEXT NOT NULL DEFAULT '',
                        actual_address TEXT NOT NULL DEFAULT '',
                        source VARCHAR(255),
                        notes TEXT,
                        registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_suppliers")
            else:
                for col, typ in [
                    ('supplier_type', 'VARCHAR(20) NOT NULL DEFAULT \'legal\''),
                    ('inn', 'VARCHAR(12)'),
                    ('kpp', 'VARCHAR(9)'),
                    ('okpo', 'VARCHAR(8)'),
                    ('ogrn', 'VARCHAR(13)'),
                    ('registration', 'TEXT'),
                    ('first_name', 'VARCHAR(255)'),
                    ('last_name', 'VARCHAR(255)'),
                    ('passport_series', 'VARCHAR(20)'),
                    ('passport_number', 'VARCHAR(20)'),
                    ('birth_date', 'DATE'),
                    ('gender', 'VARCHAR(20)'),
                    ('phone', 'VARCHAR(128) NOT NULL DEFAULT \'\''),
                    ('email', 'VARCHAR(255) NOT NULL DEFAULT \'\''),
                    ('legal_address', 'TEXT NOT NULL DEFAULT \'\''),
                    ('actual_address', 'TEXT NOT NULL DEFAULT \'\''),
                    ('source', 'VARCHAR(255)'),
                    ('notes', 'TEXT'),
                    ('registration_date', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'),
                ]:
                    if not has_col("mirror_suppliers", col):
                        cur.execute("ALTER TABLE mirror_suppliers ADD COLUMN %s %s" % (col, typ))
                        note_col("mirror_suppliers", col)
                for col, typ in [
                    ('bank_account', 'VARCHAR(20)'),
                    ('corr_account', 'VARCHAR(20)'),
                    ('bank_name', 'TEXT'),
                    ('bik', 'VARCHAR(9)'),
                ]:
                    if not has_col("mirror_suppliers", col):
                        cur.execute("ALTER TABLE mirror_suppliers ADD COLUMN %s %s" % (col, typ))
                        note_col("mirror_suppliers", col)
            if has_col("mirror_full_sheets", "name") and not has_col("mirror_full_sheets", "invoice_date"):
                cur.execute("ALTER TABLE mirror_full_sheets ADD COLUMN invoice_date DATE")
                note_col("mirror_full_sheets", "invoice_date")
            _seed_unknown_supplier(cur)
            supplier_id_col_new = False
            if not has_col("mirror_full_sheets", "supplier_id"):
                cur.execute(
                    "ALTER TABLE mirror_full_sheets ADD COLUMN supplier_id INTEGER REFERENCES mirror_suppliers(id)"
                )
                note_col("mirror_full_sheets", "supplier_id")
                supplier_id_col_new = True
            # Однократный импорт имён из текста supplier — не при каждом запуске (иначе удалённые
            # поставщики снова появляются из mirror_full_sheets.supplier).
            if ("mirror_suppliers" not in existing) or supplier_id_col_new:
                _backfill_suppliers_from_sheets(cur)
            if 'mirror_clients' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_clients (
                        id SERIAL PRIMARY KEY,
                        client_type VARCHAR(20) NOT NULL DEFAULT 'legal',
                        name VARCHAR(255) NOT NULL,
                        inn VARCHAR(12),
                        kpp VARCHAR(9),
                        okpo VARCHAR(8),
                        ogrn VARCHAR(13),
                        registration TEXT,
                        first_name VARCHAR(255),
                        last_name VARCHAR(255),
                        passport_series VARCHAR(20),
                        passport_number VARCHAR(20),
                        birth_date DATE,
                        gender VARCHAR(20),
                        phone VARCHAR(128) NOT NULL DEFAULT '',
                        email VARCHAR(255) NOT NULL DEFAULT '',
                        legal_address TEXT NOT NULL DEFAULT '',
                        actual_address TEXT NOT NULL DEFAULT '',
                        source VARCHAR(255),
                        notes TEXT,
                        registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_clients")
            else:
                for col, typ in [
                    ('phone', 'VARCHAR(128) NOT NULL DEFAULT \'\''),
                    ('email', 'VARCHAR(255) NOT NULL DEFAULT \'\''),
                    ('legal_address', 'TEXT NOT NULL DEFAULT \'\''),
                    ('actual_address', 'TEXT NOT NULL DEFAULT \'\''),
                    ('source', 'VARCHAR(255)'),
                    ('notes', 'TEXT'),
                ]:
                    if not has_col("mirror_clients", col):
                        cur.execute("ALTER TABLE mirror_clients ADD COLUMN %s %s" % (col, typ))
                        note_col("mirror_clients", col)
            # Тип клиента и поля документов: юр. лицо / ИП / физ. лицо
            for col, typ in [
                ('client_type', 'VARCHAR(20) NOT NULL DEFAULT \'legal\''),
                ('pricing_tier', 'VARCHAR(20) NOT NULL DEFAULT \'b2b\''),
                ('inn', 'VARCHAR(12)'),
                ('kpp', 'VARCHAR(9)'),
                ('okpo', 'VARCHAR(8)'),
                ('ogrn', 'VARCHAR(13)'),
                ('registration', 'TEXT'),
                ('first_name', 'VARCHAR(255)'),
                ('last_name', 'VARCHAR(255)'),
                ('passport_series', 'VARCHAR(20)'),
                ('passport_number', 'VARCHAR(20)'),
                ('birth_date', 'DATE'),
                ('gender', 'VARCHAR(20)'),
                ('registration_date', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'),
            ]:
                if not has_col("mirror_clients", col):
                    cur.execute("ALTER TABLE mirror_clients ADD COLUMN %s %s" % (col, typ))
                    note_col("mirror_clients", col)
            if 'mirror_quick_clients' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_quick_clients (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        phone VARCHAR(128) NOT NULL DEFAULT '',
                        extra_contact VARCHAR(255) NOT NULL DEFAULT '',
                        lead_source VARCHAR(64) NOT NULL DEFAULT '',
                        markup_percent INTEGER NOT NULL DEFAULT 0,
                        pricing_tier VARCHAR(32) NOT NULL DEFAULT 'b2b',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_quick_clients")
            if 'mirror_orders' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_orders (
                        id SERIAL PRIMARY KEY,
                        client_id INTEGER REFERENCES mirror_clients(id),
                        client_name VARCHAR(255),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        status VARCHAR(32) NOT NULL DEFAULT 'in_progress',
                        accepted_at TIMESTAMP,
                        notes TEXT
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_orders")
            else:
                if not has_col("mirror_orders", "client_name"):
                    cur.execute("ALTER TABLE mirror_orders ADD COLUMN client_name VARCHAR(255)")
                    note_col("mirror_orders", "client_name")
            if 'mirror_sales_orders' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_sales_orders (
                        id SERIAL PRIMARY KEY,
                        client_id INTEGER REFERENCES mirror_clients(id),
                        client_name VARCHAR(255),
                        status VARCHAR(32) NOT NULL DEFAULT 'calculated',
                        notes TEXT,
                        total_rub INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_sales_orders")
            else:
                if not has_col("mirror_sales_orders", "quick_client_id"):
                    cur.execute("ALTER TABLE mirror_sales_orders ADD COLUMN quick_client_id INTEGER")
                    note_col("mirror_sales_orders", "quick_client_id")
            if 'mirror_sales_items' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_sales_items (
                        id SERIAL PRIMARY KEY,
                        sales_order_id INTEGER NOT NULL REFERENCES mirror_sales_orders(id) ON DELETE CASCADE,
                        item_type VARCHAR(16) NOT NULL,
                        item_ref_id INTEGER,
                        item_name VARCHAR(255) NOT NULL DEFAULT '',
                        color VARCHAR(255) NOT NULL DEFAULT '',
                        qty INTEGER NOT NULL DEFAULT 1,
                        unit VARCHAR(8) NOT NULL DEFAULT 'pcs',
                        unit_price_rub INTEGER NOT NULL DEFAULT 0,
                        line_total_rub INTEGER NOT NULL DEFAULT 0
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_sales_items")
            if 'mirror_sales_profile_usage' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_sales_profile_usage (
                        id SERIAL PRIMARY KEY,
                        sales_order_id INTEGER NOT NULL REFERENCES mirror_sales_orders(id) ON DELETE CASCADE,
                        sales_item_id INTEGER REFERENCES mirror_sales_items(id) ON DELETE SET NULL,
                        profile_ref_id INTEGER,
                        consumed_stock_id INTEGER,
                        mode VARCHAR(16) NOT NULL DEFAULT 'pcs',
                        required_mm INTEGER NOT NULL DEFAULT 0,
                        rest_mm INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_sales_profile_usage")
            if 'mirror_quick_estimates' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_quick_estimates (
                        id SERIAL PRIMARY KEY,
                        category VARCHAR(32) NOT NULL DEFAULT 'glass',
                        client_id INTEGER REFERENCES mirror_clients(id),
                        client_name VARCHAR(255) NOT NULL DEFAULT '',
                        lead_source VARCHAR(64) NOT NULL DEFAULT '',
                        contact_info VARCHAR(255) DEFAULT '',
                        markup_percent INTEGER NOT NULL DEFAULT 0,
                        estimate_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        created_by_user_id INTEGER,
                        created_by_login VARCHAR(128) DEFAULT '',
                        created_by_role VARCHAR(64) DEFAULT '',
                        payload_json TEXT,
                        status VARCHAR(32) NOT NULL DEFAULT 'draft',
                        transferred_order_id INTEGER REFERENCES mirror_orders(id) ON DELETE SET NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_quick_estimates")
            else:
                if not has_col("mirror_quick_estimates", "quick_client_id"):
                    cur.execute("ALTER TABLE mirror_quick_estimates ADD COLUMN quick_client_id INTEGER")
                    note_col("mirror_quick_estimates", "quick_client_id")
            if 'mirror_production_events' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_production_events (
                        id SERIAL PRIMARY KEY,
                        order_id INTEGER NOT NULL REFERENCES mirror_orders(id) ON DELETE CASCADE,
                        event_type VARCHAR(64) NOT NULL,
                        actor_user_id INTEGER,
                        actor_login VARCHAR(128) DEFAULT '',
                        actor_role VARCHAR(64) DEFAULT '',
                        source VARCHAR(64) DEFAULT 'desktop',
                        details_json TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_production_events")
            if 'mirror_order_items' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_order_items (
                        id SERIAL PRIMARY KEY,
                        order_id INTEGER NOT NULL REFERENCES mirror_orders(id) ON DELETE CASCADE,
                        material_name VARCHAR(255) NOT NULL,
                        height_mm INTEGER NOT NULL,
                        width_mm INTEGER NOT NULL,
                        quantity INTEGER NOT NULL DEFAULT 1,
                        recipient_text VARCHAR(255),
                        edge_treatment_json TEXT
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_order_items")
            else:
                if not has_col("mirror_order_items", "edge_treatment_json"):
                    cur.execute("ALTER TABLE mirror_order_items ADD COLUMN edge_treatment_json TEXT")
                    note_col("mirror_order_items", "edge_treatment_json")
                if not has_col("mirror_order_items", "thickness_mm"):
                    cur.execute("ALTER TABLE mirror_order_items ADD COLUMN thickness_mm INTEGER NOT NULL DEFAULT 4")
                    note_col("mirror_order_items", "thickness_mm")
            if 'mirror_remnant_history' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_remnant_history (
                        id SERIAL PRIMARY KEY,
                        remnant_id INTEGER NOT NULL REFERENCES mirror_remnants(id) ON DELETE CASCADE,
                        order_id INTEGER REFERENCES mirror_orders(id),
                        action_type VARCHAR(64),
                        user_info VARCHAR(255),
                        details_json TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_remnant_history")
            if 'mirror_cut_results' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_cut_results (
                        id SERIAL PRIMARY KEY,
                        order_id INTEGER NOT NULL REFERENCES mirror_orders(id) ON DELETE CASCADE,
                        sheet_type VARCHAR(32) NOT NULL,
                        sheet_id INTEGER,
                        layout_json TEXT NOT NULL,
                        remnants_created_json TEXT
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_cut_results")
            if 'mirror_label_counter' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_label_counter (value INTEGER NOT NULL DEFAULT 0)
                """)
                cur.execute("INSERT INTO mirror_label_counter (value) VALUES (0)")
                _refresh_columns_for_table(cur, colmap, "mirror_label_counter")
            if 'mirror_k_counter' not in existing:
                cur.execute("CREATE TABLE mirror_k_counter (value INTEGER NOT NULL DEFAULT 0)")
                cur.execute("INSERT INTO mirror_k_counter (value) VALUES (0)")
                _refresh_columns_for_table(cur, colmap, "mirror_k_counter")
            # k_number в заказах: префикс K (K1, K2...) — продукт для клиента
            if not has_col("mirror_orders", "k_number"):
                cur.execute("ALTER TABLE mirror_orders ADD COLUMN k_number INTEGER")
                note_col("mirror_orders", "k_number")
            # Стекло/зеркало: тип заказа и сохранённый просчёт блоков (JSON)
            for col, typ in [
                ('order_kind', 'VARCHAR(64)'),
                ('blocks_calc_json', 'TEXT'),
            ]:
                if not has_col("mirror_orders", col):
                    cur.execute("ALTER TABLE mirror_orders ADD COLUMN %s %s" % (col, typ))
                    note_col("mirror_orders", col)
            if not has_col("mirror_orders", "quick_client_id"):
                cur.execute("ALTER TABLE mirror_orders ADD COLUMN quick_client_id INTEGER")
                note_col("mirror_orders", "quick_client_id")
            for col, typ in (
                ("created_by_user_id", "INTEGER"),
                ("created_by_login", "VARCHAR(128) DEFAULT ''"),
                ("created_by_role", "VARCHAR(64) DEFAULT ''"),
            ):
                if not has_col("mirror_orders", col):
                    cur.execute("ALTER TABLE mirror_orders ADD COLUMN %s %s" % (col, typ))
                    note_col("mirror_orders", col)
            # Порядковой номер этикетки на остатках (1, 2, 3...) — без повторений
            if 'mirror_remnants' in existing:
                if not has_col("mirror_remnants", "label_number"):
                    cur.execute("ALTER TABLE mirror_remnants ADD COLUMN label_number INTEGER")
                    note_col("mirror_remnants", "label_number")
                if not has_col("mirror_remnants", "thickness_mm"):
                    cur.execute("ALTER TABLE mirror_remnants ADD COLUMN thickness_mm INTEGER NOT NULL DEFAULT 4")
                    note_col("mirror_remnants", "thickness_mm")
                if not has_col("mirror_remnants", "reserved_for_cut_order_id"):
                    cur.execute("ALTER TABLE mirror_remnants ADD COLUMN reserved_for_cut_order_id INTEGER")
                    note_col("mirror_remnants", "reserved_for_cut_order_id")
            # Архив удалённых остатков: лист и характеристики на момент удаления, дата/время удаления
            if 'mirror_deleted_remnants' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_deleted_remnants (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        height_mm INTEGER NOT NULL,
                        width_mm INTEGER NOT NULL,
                        thickness_mm INTEGER NOT NULL DEFAULT 4,
                        unique_number VARCHAR(64),
                        label_number INTEGER,
                        deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        original_remnant_id INTEGER,
                        created_in_cut_archive_id INTEGER,
                        deleted_by_login VARCHAR(128),
                        deleted_by_display VARCHAR(255)
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_deleted_remnants")
            else:
                if not has_col("mirror_deleted_remnants", "original_remnant_id"):
                    cur.execute("ALTER TABLE mirror_deleted_remnants ADD COLUMN original_remnant_id INTEGER")
                    note_col("mirror_deleted_remnants", "original_remnant_id")
                if not has_col("mirror_deleted_remnants", "created_in_cut_archive_id"):
                    cur.execute("ALTER TABLE mirror_deleted_remnants ADD COLUMN created_in_cut_archive_id INTEGER")
                    note_col("mirror_deleted_remnants", "created_in_cut_archive_id")
                if not has_col("mirror_deleted_remnants", "deleted_by_login"):
                    cur.execute("ALTER TABLE mirror_deleted_remnants ADD COLUMN deleted_by_login VARCHAR(128)")
                    note_col("mirror_deleted_remnants", "deleted_by_login")
                if not has_col("mirror_deleted_remnants", "deleted_by_display"):
                    cur.execute("ALTER TABLE mirror_deleted_remnants ADD COLUMN deleted_by_display VARCHAR(255)")
                    note_col("mirror_deleted_remnants", "deleted_by_display")
            if 'mirror_deleted_full_sheets' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_deleted_full_sheets (
                        id SERIAL PRIMARY KEY,
                        sheet_id INTEGER,
                        name VARCHAR(255) NOT NULL DEFAULT '',
                        height_mm INTEGER NOT NULL DEFAULT 0,
                        width_mm INTEGER NOT NULL DEFAULT 0,
                        thickness_mm INTEGER NOT NULL DEFAULT 4,
                        quantity INTEGER NOT NULL DEFAULT 1,
                        supplier VARCHAR(255),
                        cost NUMERIC(12, 2),
                        warehouse_number VARCHAR(64),
                        deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        deleted_by_login VARCHAR(128),
                        deleted_by_display VARCHAR(255)
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_deleted_full_sheets")
            # Архив резов: из какого листа, когда, для какого клиента, что вырезано и что осталось
            if 'mirror_cut_archive' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_cut_archive (
                        id SERIAL PRIMARY KEY,
                        order_id INTEGER NOT NULL REFERENCES mirror_orders(id) ON DELETE CASCADE,
                        client_name VARCHAR(255),
                        cut_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        sheet_type VARCHAR(32) NOT NULL,
                        sheet_id INTEGER,
                        sheet_name VARCHAR(255) NOT NULL,
                        sheet_height_mm INTEGER NOT NULL,
                        sheet_width_mm INTEGER NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_cut_archive")
            if 'mirror_cut_archive_detail' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_cut_archive_detail (
                        id SERIAL PRIMARY KEY,
                        cut_archive_id INTEGER NOT NULL REFERENCES mirror_cut_archive(id) ON DELETE CASCADE,
                        item_kind VARCHAR(16) NOT NULL,
                        width_mm INTEGER NOT NULL,
                        height_mm INTEGER NOT NULL,
                        recipient VARCHAR(255),
                        remnant_id INTEGER REFERENCES mirror_remnants(id) ON DELETE SET NULL
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_cut_archive_detail")
            if 'mirror_layout_training' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_layout_training (
                        id SERIAL PRIMARY KEY,
                        sheet_width_mm INTEGER NOT NULL,
                        sheet_height_mm INTEGER NOT NULL,
                        pieces_json TEXT NOT NULL,
                        source VARCHAR(32) NOT NULL DEFAULT 'training_tab',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_layout_training")
            if 'mirror_inventory_campaigns' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_inventory_campaigns (
                        id SERIAL PRIMARY KEY,
                        status VARCHAR(16) NOT NULL DEFAULT 'active',
                        glass_type_keys TEXT NOT NULL DEFAULT '[]',
                        profile_type_keys TEXT NOT NULL DEFAULT '[]',
                        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        completed_at TIMESTAMP,
                        started_by_user_id INTEGER,
                        started_by_login VARCHAR(128) DEFAULT '',
                        summary_json TEXT NOT NULL DEFAULT '{}'
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_inv_campaign_status ON mirror_inventory_campaigns(status)"
                )
                _refresh_columns_for_table(cur, colmap, "mirror_inventory_campaigns")
            if 'mirror_inventory_type_completion' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_inventory_type_completion (
                        id SERIAL PRIMARY KEY,
                        domain VARCHAR(16) NOT NULL,
                        type_key VARCHAR(512) NOT NULL,
                        last_completed_campaign_id INTEGER REFERENCES mirror_inventory_campaigns(id) ON DELETE SET NULL,
                        completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(domain, type_key)
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_inv_type_completion_domain ON mirror_inventory_type_completion(domain)"
                )
                _refresh_columns_for_table(cur, colmap, "mirror_inventory_type_completion")
            if 'mirror_inventory_scans' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_inventory_scans (
                        id SERIAL PRIMARY KEY,
                        item_type VARCHAR(16) NOT NULL,
                        stock_ref_id INTEGER,
                        unique_number VARCHAR(64) NOT NULL DEFAULT '',
                        size_text VARCHAR(128) NOT NULL DEFAULT '',
                        session_key VARCHAR(64) NOT NULL DEFAULT '',
                        campaign_id INTEGER REFERENCES mirror_inventory_campaigns(id) ON DELETE SET NULL,
                        actor_user_id INTEGER,
                        actor_login VARCHAR(128) DEFAULT '',
                        scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_inventory_scans_campaign ON mirror_inventory_scans(campaign_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_inventory_scans_campaign_item_number "
                    "ON mirror_inventory_scans(campaign_id, item_type, unique_number) "
                    "WHERE COALESCE(unique_number, '') <> ''"
                )
                _refresh_columns_for_table(cur, colmap, "mirror_inventory_scans")
            else:
                if not has_col("mirror_inventory_scans", "campaign_id"):
                    cur.execute(
                        """
                        ALTER TABLE mirror_inventory_scans
                        ADD COLUMN campaign_id INTEGER REFERENCES mirror_inventory_campaigns(id) ON DELETE SET NULL
                        """
                    )
                    note_col("mirror_inventory_scans", "campaign_id")
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_inventory_scans_campaign ON mirror_inventory_scans(campaign_id)"
                    )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_inventory_scans_campaign_item_number "
                    "ON mirror_inventory_scans(campaign_id, item_type, unique_number) "
                    "WHERE COALESCE(unique_number, '') <> ''"
                )
            if 'mirror_inventory_losses' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_inventory_losses (
                        id SERIAL PRIMARY KEY,
                        item_type VARCHAR(16) NOT NULL,
                        stock_ref_id INTEGER,
                        unique_number VARCHAR(64) NOT NULL DEFAULT '',
                        reason_text TEXT NOT NULL DEFAULT '',
                        session_key VARCHAR(64) NOT NULL DEFAULT '',
                        campaign_id INTEGER REFERENCES mirror_inventory_campaigns(id) ON DELETE SET NULL,
                        actor_user_id INTEGER,
                        actor_login VARCHAR(128) DEFAULT '',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_inventory_losses_campaign ON mirror_inventory_losses(campaign_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_inventory_losses_campaign_item_number "
                    "ON mirror_inventory_losses(campaign_id, item_type, unique_number) "
                    "WHERE COALESCE(unique_number, '') <> ''"
                )
                _refresh_columns_for_table(cur, colmap, "mirror_inventory_losses")
            else:
                if not has_col("mirror_inventory_losses", "campaign_id"):
                    cur.execute(
                        """
                        ALTER TABLE mirror_inventory_losses
                        ADD COLUMN campaign_id INTEGER REFERENCES mirror_inventory_campaigns(id) ON DELETE SET NULL
                        """
                    )
                    note_col("mirror_inventory_losses", "campaign_id")
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_inventory_losses_campaign ON mirror_inventory_losses(campaign_id)"
                    )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_inventory_losses_campaign_item_number "
                    "ON mirror_inventory_losses(campaign_id, item_type, unique_number) "
                    "WHERE COALESCE(unique_number, '') <> ''"
                )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_inv_campaign_status_id_desc "
                "ON mirror_inventory_campaigns(status, id DESC)"
            )
            if 'mirror_generated_qr_log' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_generated_qr_log (
                        id SERIAL PRIMARY KEY,
                        source_kind VARCHAR(16) NOT NULL,
                        label_code VARCHAR(128) NOT NULL DEFAULT '',
                        title VARCHAR(256) NOT NULL DEFAULT '',
                        subtitle VARCHAR(512) NOT NULL DEFAULT '',
                        actor_user_id INTEGER,
                        actor_login VARCHAR(128) DEFAULT '',
                        actor_name VARCHAR(256) DEFAULT '',
                        details_json TEXT NOT NULL DEFAULT '{}',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_generated_qr_source_created "
                    "ON mirror_generated_qr_log(source_kind, created_at DESC)"
                )
                _refresh_columns_for_table(cur, colmap, "mirror_generated_qr_log")
            if 'mirror_cut_batches' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_cut_batches (
                        id SERIAL PRIMARY KEY,
                        material_key VARCHAR(512) NOT NULL DEFAULT '',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        created_by_user_id INTEGER,
                        created_by_login VARCHAR(128) DEFAULT ''
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_cut_batches")
            if 'mirror_cut_batch_sheets' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_cut_batch_sheets (
                        id SERIAL PRIMARY KEY,
                        batch_id INTEGER NOT NULL REFERENCES mirror_cut_batches(id) ON DELETE CASCADE,
                        sheet_type VARCHAR(32) NOT NULL,
                        sheet_id INTEGER,
                        layout_json TEXT NOT NULL DEFAULT '{}',
                        remnants_created_json TEXT NOT NULL DEFAULT '[]'
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_cut_batch_sheets")
            if 'mirror_cut_batch_pieces' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_cut_batch_pieces (
                        id SERIAL PRIMARY KEY,
                        batch_sheet_id INTEGER NOT NULL REFERENCES mirror_cut_batch_sheets(id) ON DELETE CASCADE,
                        order_id INTEGER NOT NULL REFERENCES mirror_orders(id) ON DELETE CASCADE,
                        product_id VARCHAR(64) NOT NULL DEFAULT ''
                    )
                """)
                _refresh_columns_for_table(cur, colmap, "mirror_cut_batch_pieces")
            if 'mirror_profile_cut_events' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_profile_cut_events (
                        id SERIAL PRIMARY KEY,
                        stock_id INTEGER,
                        order_id INTEGER,
                        batch_id INTEGER,
                        event_type VARCHAR(40) NOT NULL DEFAULT 'history_event',
                        reason_text TEXT DEFAULT '',
                        actor_user_id INTEGER,
                        actor_login VARCHAR(128) DEFAULT '',
                        actor_role VARCHAR(32) DEFAULT '',
                        payload_json TEXT DEFAULT '{}',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_cut_events_stock_created ON mirror_profile_cut_events(stock_id, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_cut_events_order_created ON mirror_profile_cut_events(order_id, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_cut_events_batch ON mirror_profile_cut_events(batch_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_cut_events_type ON mirror_profile_cut_events(event_type)")
                _refresh_columns_for_table(cur, colmap, "mirror_profile_cut_events")
            if 'mirror_desktop_app_release' not in existing:
                cur.execute(
                    """
                    CREATE TABLE mirror_desktop_app_release (
                        id SERIAL PRIMARY KEY,
                        channel VARCHAR(64) NOT NULL DEFAULT 'mirrorcut',
                        version VARCHAR(64) NOT NULL DEFAULT '',
                        manifest_url TEXT NOT NULL DEFAULT '',
                        manifest_json JSONB,
                        released_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        active BOOLEAN NOT NULL DEFAULT TRUE
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_mirror_desktop_app_release_channel_active "
                    "ON mirror_desktop_app_release (channel, active, released_at DESC)"
                )
                _refresh_columns_for_table(cur, colmap, "mirror_desktop_app_release")
            # Порог минимального отхода 250×250 мм для перечня материалов (если записи нет — вставляем)
            for col, typ in [
                ('bank_account', 'VARCHAR(20)'),
                ('corr_account', 'VARCHAR(20)'),
                ('bank_name', 'VARCHAR(255)'),
                ('bik', 'VARCHAR(9)'),
                ('ogrnip', 'VARCHAR(15)'),
            ]:
                if not has_col("mirror_clients", col):
                    cur.execute("ALTER TABLE mirror_clients ADD COLUMN %s %s" % (col, typ))
                    note_col("mirror_clients", col)
            if 'mirror_client_entities' not in existing:
                cur.execute("""
                    CREATE TABLE mirror_client_entities (
                        id SERIAL PRIMARY KEY,
                        owner_client_id INTEGER NOT NULL REFERENCES mirror_clients(id) ON DELETE CASCADE,
                        entity_type VARCHAR(20) NOT NULL DEFAULT 'legal',
                        display_name VARCHAR(255) NOT NULL DEFAULT '',
                        inn VARCHAR(12),
                        kpp VARCHAR(9),
                        okpo VARCHAR(10),
                        ogrn VARCHAR(13),
                        ogrnip VARCHAR(15),
                        bank_account VARCHAR(20),
                        corr_account VARCHAR(20),
                        bank_name VARCHAR(255),
                        bik VARCHAR(9),
                        is_default BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_client_entities_owner "
                    "ON mirror_client_entities(owner_client_id)"
                )
                _refresh_columns_for_table(cur, colmap, "mirror_client_entities")
            if not has_col("mirror_orders", "billing_entity_id"):
                cur.execute(
                    "ALTER TABLE mirror_orders ADD COLUMN billing_entity_id INTEGER "
                    "REFERENCES mirror_client_entities(id)"
                )
                note_col("mirror_orders", "billing_entity_id")
            _seed_materials_thresholds(cur)
            _migrate_checked_qr_status(cur)
            # Удалить материал SERGEY SPACE везде
            _remove_sergey_space(cur)