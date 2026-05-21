-- Прайс скругления углов (как в Streamlit corner_rounding_price).
-- Если таблица уже есть в вашей БД — этот файл можно не применять.

CREATE TABLE IF NOT EXISTS corner_rounding_price (
    thickness INTEGER PRIMARY KEY,
    r_3_10 INTEGER NOT NULL DEFAULT 0,
    r_11_20 INTEGER NOT NULL DEFAULT 0,
    r_21_35 INTEGER NOT NULL DEFAULT 0,
    r_36_50 INTEGER NOT NULL DEFAULT 0,
    r_51_100 INTEGER NOT NULL DEFAULT 0
);

-- Пример строк (замените цены на свои из рабочей базы).
INSERT INTO corner_rounding_price (thickness, r_3_10, r_11_20, r_21_35, r_36_50, r_51_100)
VALUES
    (4, 200, 400, 600, 800, 1000),
    (5, 220, 440, 660, 880, 1100),
    (6, 240, 480, 720, 960, 1200),
    (8, 280, 560, 840, 1120, 1400),
    (10, 320, 640, 960, 1280, 1600)
ON CONFLICT (thickness) DO NOTHING;
