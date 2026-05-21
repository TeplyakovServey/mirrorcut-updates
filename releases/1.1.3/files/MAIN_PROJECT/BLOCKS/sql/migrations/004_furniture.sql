-- Справочник фурнитуры для калькулятора BLOCKS (цены из БД).
-- Выполнить один раз на базе. Повторный INSERT добавит дубликаты — при повторе: TRUNCATE blocks_furniture RESTART IDENTITY;
CREATE TABLE IF NOT EXISTS blocks_furniture (
    id SERIAL PRIMARY KEY,
    name VARCHAR(512) NOT NULL,
    color VARCHAR(256) NOT NULL DEFAULT '',
    price_legal INTEGER NOT NULL DEFAULT 0,
    price_individual INTEGER NOT NULL DEFAULT 0,
    photo_base VARCHAR(256) NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_blocks_furniture_name ON blocks_furniture (name);

-- Однократная заливка: повторный запуск даст дубликаты по id — при необходимости TRUNCATE перед INSERT.
INSERT INTO blocks_furniture (name, color, price_legal, price_individual, photo_base) VALUES
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
('Держатель 5.10 Д', 'Черный глянец', 250, 350, 'derzh_5_10D_2021-chernyy');
