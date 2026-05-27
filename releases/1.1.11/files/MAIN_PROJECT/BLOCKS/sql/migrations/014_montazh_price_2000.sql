-- Цена монтажа (вкладка «Работы» → delivery_price).
INSERT INTO delivery_price (name, price)
SELECT 'Монтаж', 2000
WHERE NOT EXISTS (SELECT 1 FROM delivery_price WHERE name = 'Монтаж');
