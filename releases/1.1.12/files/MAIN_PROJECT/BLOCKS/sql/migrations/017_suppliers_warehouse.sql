-- Поставщики: банковские реквизиты; склад: дата накладной; seed «Неопознанный».
-- Колонки mirror_* добавляются также через db/migrations.py при старте приложения.

INSERT INTO mirror_suppliers (supplier_type, name, phone, email, legal_address, actual_address, notes)
SELECT 'legal', 'Неопознанный', '', '', '', '', 'Системный поставщик для неопознанных поставок'
WHERE NOT EXISTS (
    SELECT 1 FROM mirror_suppliers WHERE LOWER(TRIM(name)) = LOWER('Неопознанный')
);
