-- Цены вырезов по сложности (калькулятор BLOCKS).

CREATE TABLE IF NOT EXISTS blocks_virez_prices (
    id SERIAL PRIMARY KEY,
    category_code VARCHAR(32) NOT NULL,
    title_ru VARCHAR(128) NOT NULL,
    price_rub INTEGER NOT NULL CHECK (price_rub >= 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_blocks_virez_prices_code
    ON blocks_virez_prices (category_code);

INSERT INTO blocks_virez_prices (category_code, title_ru, price_rub) VALUES
    ('simple', 'Простой', 1500),
    ('medium', 'Средний', 3000),
    ('complex', 'Сложный', 5000)
ON CONFLICT (category_code) DO UPDATE SET
    title_ru = EXCLUDED.title_ru,
    price_rub = EXCLUDED.price_rub;
