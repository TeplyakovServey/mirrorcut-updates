-- Зеркало серебро б/цв 5 мм: цена по интерполяции между 4 и 6 мм (округление вверх).
INSERT INTO materials (material_type, material_variant, thickness, price, status_zakalka)
SELECT
    'Зеркало',
    v.variant,
    5,
    CEIL((p4.price + p6.price) / 2.0)::integer,
    COALESCE(p4.status_zakalka, p6.status_zakalka, 0)
FROM (
    SELECT DISTINCT material_variant AS variant
    FROM materials
    WHERE material_type = 'Зеркало'
      AND (
          LOWER(REPLACE(material_variant, '\', '/')) LIKE '%серебро%б/цв%'
          OR LOWER(material_variant) LIKE '%серебро%б\\цв%'
      )
) v
JOIN LATERAL (
    SELECT price, status_zakalka FROM materials m4
    WHERE m4.material_type = 'Зеркало' AND m4.material_variant = v.variant AND m4.thickness = 4
    LIMIT 1
) p4 ON TRUE
JOIN LATERAL (
    SELECT price, status_zakalka FROM materials m6
    WHERE m6.material_type = 'Зеркало' AND m6.material_variant = v.variant AND m6.thickness = 6
    LIMIT 1
) p6 ON TRUE
WHERE NOT EXISTS (
    SELECT 1 FROM materials ex
    WHERE ex.material_type = 'Зеркало' AND ex.material_variant = v.variant AND ex.thickness = 5
);
