-- Зеркало серебро бесцветное 5 мм: 2100 ₽/м², закалка 0 (см. также 017_mirror_silver_variant_canonical.sql).
INSERT INTO materials (material_type, material_variant, thickness, price, status_zakalka)
SELECT 'Зеркало', 'серебро бесцветное', 5, 2100, 0
WHERE NOT EXISTS (
    SELECT 1 FROM materials
    WHERE material_type = 'Зеркало' AND material_variant = 'серебро бесцветное' AND thickness = 5
);

UPDATE materials
SET price = 2100, status_zakalka = 0
WHERE material_type = 'Зеркало' AND material_variant = 'серебро бесцветное' AND thickness = 5;
