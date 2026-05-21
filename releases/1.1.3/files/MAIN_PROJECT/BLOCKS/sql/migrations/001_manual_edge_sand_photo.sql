-- Ручная обработка кромки (фикс за толщину при короткой стороне < 75 мм)
CREATE TABLE IF NOT EXISTS manual_edge_processing_price (
    thickness_mm INTEGER PRIMARY KEY,
    price_rub INTEGER NOT NULL
);

INSERT INTO manual_edge_processing_price (thickness_mm, price_rub) VALUES
    (4, 300), (5, 320), (6, 350), (8, 450), (10, 450)
ON CONFLICT (thickness_mm) DO UPDATE SET price_rub = EXCLUDED.price_rub;

-- Пескоструй (типы в нижнем регистре как в Streamlit)
CREATE TABLE IF NOT EXISTS sandblasting_price (
    type TEXT PRIMARY KEY,
    price INTEGER NOT NULL
);

-- Старые БД могли создать таблицу без PRIMARY KEY — без ON CONFLICT
UPDATE sandblasting_price SET price = 1500 WHERE type = 'сплошное матирование';
INSERT INTO sandblasting_price (type, price) SELECT 'сплошное матирование', 1500 WHERE NOT EXISTS (SELECT 1 FROM sandblasting_price WHERE type = 'сплошное матирование');
UPDATE sandblasting_price SET price = 2640 WHERE type = 'рисунок';
INSERT INTO sandblasting_price (type, price) SELECT 'рисунок', 2640 WHERE NOT EXISTS (SELECT 1 FROM sandblasting_price WHERE type = 'рисунок');
UPDATE sandblasting_price SET price = 500 WHERE type = 'пескоструйная кнопка';
INSERT INTO sandblasting_price (type, price) SELECT 'пескоструйная кнопка', 500 WHERE NOT EXISTS (SELECT 1 FROM sandblasting_price WHERE type = 'пескоструйная кнопка');
UPDATE sandblasting_price SET price = 2640 WHERE type = 'полосы зсп';
INSERT INTO sandblasting_price (type, price) SELECT 'полосы зсп', 2640 WHERE NOT EXISTS (SELECT 1 FROM sandblasting_price WHERE type = 'полосы зсп');

-- Загрузки фотопечати (байты в БД)
CREATE TABLE IF NOT EXISTS photo_print_uploads (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    mime_type TEXT,
    file_name TEXT,
    data BYTEA NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_photo_print_uploads_created ON photo_print_uploads (created_at DESC);
