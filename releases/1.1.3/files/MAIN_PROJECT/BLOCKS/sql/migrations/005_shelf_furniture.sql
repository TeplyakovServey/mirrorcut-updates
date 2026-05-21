-- Полкодержатели Grace / Kristal: ссылка на МДМ, толщина стекла, флаг «полка», картинка по номеру в img_fur (1.png …).
-- Цены (price_*) изначально 0 — заполнить скриптом 005_parse_shelf_furniture_prices.py

ALTER TABLE blocks_furniture ADD COLUMN IF NOT EXISTS source_url TEXT NOT NULL DEFAULT '';
ALTER TABLE blocks_furniture ADD COLUMN IF NOT EXISTS thickness_mm INTEGER NULL;
ALTER TABLE blocks_furniture ADD COLUMN IF NOT EXISTS is_shelf_holder BOOLEAN NOT NULL DEFAULT FALSE;

INSERT INTO blocks_furniture (name, color, price_legal, price_individual, photo_base, source_url, thickness_mm, is_shelf_holder)
VALUES
('Grace', 'Хром', 0, 0, '1', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/122031/', 6, TRUE),
('Grace', 'Никель', 0, 0, '2', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/122032/', 6, TRUE),
('Grace', 'Черный никель', 0, 0, '3', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/122030/', 6, TRUE),
('Grace', 'Хром', 0, 0, '1', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/122025/', 8, TRUE),
('Grace', 'Никель', 0, 0, '2', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/122026/', 8, TRUE),
('Grace', 'Черный никель', 0, 0, '3', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/122023/', 8, TRUE),
('Kristal', 'Хром', 0, 0, '4', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/10468/', 6, TRUE),
('Kristal', 'Никель', 0, 0, '5', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/10469/', 6, TRUE),
('Kristal', 'Хром', 0, 0, '4', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/10471/', 8, TRUE),
('Kristal', 'Никель', 0, 0, '5', 'https://www.mdm-complect.ru/catalog/polkoderzhateli/10472/', 8, TRUE);
