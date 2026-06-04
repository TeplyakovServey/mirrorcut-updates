-- Идемпотентная инициализация справочника фурнитуры BLOCKS (если таблицы не было — как на вашей БД).
-- Запуск: psql -f 006_blocks_furniture_bootstrap.sql  ИЛИ  python apply_006_blocks_furniture_bootstrap.py

CREATE TABLE IF NOT EXISTS blocks_furniture (
    id SERIAL PRIMARY KEY,
    name VARCHAR(512) NOT NULL,
    color VARCHAR(256) NOT NULL DEFAULT '',
    price_legal INTEGER NOT NULL DEFAULT 0,
    price_individual INTEGER NOT NULL DEFAULT 0,
    photo_base VARCHAR(256) NOT NULL DEFAULT ''
);

ALTER TABLE blocks_furniture ADD COLUMN IF NOT EXISTS source_url TEXT NOT NULL DEFAULT '';
ALTER TABLE blocks_furniture ADD COLUMN IF NOT EXISTS thickness_mm INTEGER NULL;
ALTER TABLE blocks_furniture ADD COLUMN IF NOT EXISTS is_shelf_holder BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_blocks_furniture_name ON blocks_furniture (name);

-- Базовые держатели (без полки): не дублировать по (name, color, photo_base)
INSERT INTO blocks_furniture (name, color, price_legal, price_individual, photo_base)
SELECT v.name, v.color, v.pl, v.pi, v.pb
FROM (
    VALUES
        ('Держатель 5.01 Д, D=16 мм, (БГ), S=6-8 мм', 'Белый глянец', 250, 350, '5.01-BG-D_16-6_8'),
        ('Держатель 5.01 Д, D=16 мм, (БГ)', 'Белый глянец', 250, 350, '5.01-BG-D_16'),
        ('Держатель 5.01 Д, D=22 мм, (001)', 'Хром', 250, 350, '5-.01-D-tsvet-kor'),
        ('Держатель 5.01 Д, D=16 мм, (ЧМ)', 'Черный матовый', 250, 350, '5.01-CHM-D_16-_6_8_'),
        ('Держатель 5.01 Д, D=16 мм, (ЧМ), S=6-8 мм', 'Черный матовый', 250, 350, '5.01-CHM-D_16'),
        ('Держатель 5.01 Д, D=16 мм, (001)', 'Хром', 250, 350, '5-.01-D-tsvet-kor'),
        ('Держатель 5.10 Д, (001)', 'Хром', 250, 350, '5-.10-D-dorbotka-tsveta-'),
        ('Держатель 5.01, D=16 мм', 'Никель', 250, 350, '5.01-D16_1'),
        ('Держатель 5.01, D=16 мм', 'Никель матовый', 250, 350, '5.01-D16_3'),
        ('Держатель 5.01, D=16 мм', 'Золото', 250, 350, '5.01-D16_2'),
        ('Держатель 5.10 Д', 'Черный глянец', 250, 350, 'derzh_5_10D_2021-chernyy')
) AS v(name, color, pl, pi, pb)
WHERE NOT EXISTS (
    SELECT 1 FROM blocks_furniture b
    WHERE b.name = v.name AND b.color = v.color AND b.photo_base = v.pb
        AND COALESCE(b.is_shelf_holder, FALSE) = FALSE
);

-- Полкодержатели Grace / Kristal, 6 и 8 мм; цены 0 до парсера МДМ
INSERT INTO blocks_furniture (name, color, price_legal, price_individual, photo_base, source_url, thickness_mm, is_shelf_holder)
SELECT 'Grace', 'Хром', 0, 0, '1', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/122031/', 6, TRUE
WHERE NOT EXISTS (SELECT 1 FROM blocks_furniture WHERE source_url = 'https://www.mdm-complect.ru/catalog/polkoderzhateli/122031/');

INSERT INTO blocks_furniture (name, color, price_legal, price_individual, photo_base, source_url, thickness_mm, is_shelf_holder)
SELECT 'Grace', 'Никель', 0, 0, '2', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/122032/', 6, TRUE
WHERE NOT EXISTS (SELECT 1 FROM blocks_furniture WHERE source_url = 'https://www.mdm-complect.ru/catalog/polkoderzhateli/122032/');

INSERT INTO blocks_furniture (name, color, price_legal, price_individual, photo_base, source_url, thickness_mm, is_shelf_holder)
SELECT 'Grace', 'Черный никель', 0, 0, '3', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/122030/', 6, TRUE
WHERE NOT EXISTS (SELECT 1 FROM blocks_furniture WHERE source_url = 'https://www.mdm-complect.ru/catalog/polkoderzhateli/122030/');

INSERT INTO blocks_furniture (name, color, price_legal, price_individual, photo_base, source_url, thickness_mm, is_shelf_holder)
SELECT 'Grace', 'Хром', 0, 0, '1', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/122025/', 8, TRUE
WHERE NOT EXISTS (SELECT 1 FROM blocks_furniture WHERE source_url = 'https://www.mdm-complect.ru/catalog/polkoderzhateli/122025/');

INSERT INTO blocks_furniture (name, color, price_legal, price_individual, photo_base, source_url, thickness_mm, is_shelf_holder)
SELECT 'Grace', 'Никель', 0, 0, '2', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/122026/', 8, TRUE
WHERE NOT EXISTS (SELECT 1 FROM blocks_furniture WHERE source_url = 'https://www.mdm-complect.ru/catalog/polkoderzhateli/122026/');

INSERT INTO blocks_furniture (name, color, price_legal, price_individual, photo_base, source_url, thickness_mm, is_shelf_holder)
SELECT 'Grace', 'Черный никель', 0, 0, '3', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/122023/', 8, TRUE
WHERE NOT EXISTS (SELECT 1 FROM blocks_furniture WHERE source_url = 'https://www.mdm-complect.ru/catalog/polkoderzhateli/122023/');

INSERT INTO blocks_furniture (name, color, price_legal, price_individual, photo_base, source_url, thickness_mm, is_shelf_holder)
SELECT 'Kristal', 'Хром', 0, 0, '4', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/10468/', 6, TRUE
WHERE NOT EXISTS (SELECT 1 FROM blocks_furniture WHERE source_url = 'https://www.mdm-complect.ru/catalog/polkoderzhateli/10468/');

INSERT INTO blocks_furniture (name, color, price_legal, price_individual, photo_base, source_url, thickness_mm, is_shelf_holder)
SELECT 'Kristal', 'Никель', 0, 0, '5', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/10469/', 6, TRUE
WHERE NOT EXISTS (SELECT 1 FROM blocks_furniture WHERE source_url = 'https://www.mdm-complect.ru/catalog/polkoderzhateli/10469/');

INSERT INTO blocks_furniture (name, color, price_legal, price_individual, photo_base, source_url, thickness_mm, is_shelf_holder)
SELECT 'Kristal', 'Хром', 0, 0, '4', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/10471/', 8, TRUE
WHERE NOT EXISTS (SELECT 1 FROM blocks_furniture WHERE source_url = 'https://www.mdm-complect.ru/catalog/polkoderzhateli/10471/');

INSERT INTO blocks_furniture (name, color, price_legal, price_individual, photo_base, source_url, thickness_mm, is_shelf_holder)
SELECT 'Kristal', 'Никель', 0, 0, '5', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/10472/', 8, TRUE
WHERE NOT EXISTS (SELECT 1 FROM blocks_furniture WHERE source_url = 'https://www.mdm-complect.ru/catalog/polkoderzhateli/10472/');
