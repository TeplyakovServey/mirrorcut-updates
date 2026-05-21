-- Базовая стоимость доставки за пределами КАД (до учёта ₽/км): 2000 ₽ (таблица delivery_price).
UPDATE delivery_price SET price = 2000 WHERE name = 'За КАД база';
