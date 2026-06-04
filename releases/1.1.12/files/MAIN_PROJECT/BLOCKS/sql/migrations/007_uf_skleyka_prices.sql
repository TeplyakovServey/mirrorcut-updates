-- Цены УФ-склейки: руб/м по толщине материала и фикс за 1 петлю (наклейка / снятие).
-- Строка thickness_mm = 0 — только цены петель (price_per_meter_rub = 0).

CREATE TABLE IF NOT EXISTS blocks_uf_skleyka_prices (
    id SERIAL PRIMARY KEY,
    thickness_mm INTEGER NOT NULL,
    price_per_meter_rub INTEGER NOT NULL DEFAULT 0,
    hinge_paste_one_rub INTEGER NOT NULL DEFAULT 0,
    hinge_remove_one_rub INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_blocks_uf_skleyka_thickness
    ON blocks_uf_skleyka_prices (thickness_mm);

INSERT INTO blocks_uf_skleyka_prices (thickness_mm, price_per_meter_rub, hinge_paste_one_rub, hinge_remove_one_rub)
VALUES
    (4, 1320, 0, 0),
    (5, 1320, 0, 0),
    (6, 1320, 0, 0),
    (8, 1320, 0, 0),
    (10, 1320, 0, 0),
    (0, 0, 320, 320)
ON CONFLICT (thickness_mm) DO UPDATE SET
    price_per_meter_rub = EXCLUDED.price_per_meter_rub,
    hinge_paste_one_rub = EXCLUDED.hinge_paste_one_rub,
    hinge_remove_one_rub = EXCLUDED.hinge_remove_one_rub;
