-- Единое имя варианта зеркала: «серебро бесцветное» (вместо «серебро б\цв» и т.п.).
-- При дубликате по толщине оставляем строку с каноническим именем.

UPDATE materials
SET material_variant = 'серебро бесцветное'
WHERE material_type = 'Зеркало'
  AND material_variant <> 'серебро бесцветное'
  AND (
      LOWER(material_variant) LIKE '%серебро%бесцвет%'
      OR LOWER(REPLACE(material_variant, E'\\', '/')) LIKE '%серебро%б/цв%'
      OR LOWER(material_variant) LIKE '%серебро%бцв%'
  )
  AND NOT EXISTS (
      SELECT 1 FROM materials c
      WHERE c.material_type = 'Зеркало'
        AND c.material_variant = 'серебро бесцветное'
        AND c.thickness = materials.thickness
  );

DELETE FROM materials a
WHERE a.material_type = 'Зеркало'
  AND a.material_variant <> 'серебро бесцветное'
  AND (
      LOWER(a.material_variant) LIKE '%серебро%бесцвет%'
      OR LOWER(REPLACE(a.material_variant, E'\\', '/')) LIKE '%серебро%б/цв%'
      OR LOWER(a.material_variant) LIKE '%серебро%бцв%'
  )
  AND EXISTS (
      SELECT 1 FROM materials c
      WHERE c.material_type = 'Зеркало'
        AND c.material_variant = 'серебро бесцветное'
        AND c.thickness = a.thickness
  );

INSERT INTO materials (material_type, material_variant, thickness, price, status_zakalka)
SELECT 'Зеркало', 'серебро бесцветное', 5, 2100, 0
WHERE NOT EXISTS (
    SELECT 1 FROM materials
    WHERE material_type = 'Зеркало' AND material_variant = 'серебро бесцветное' AND thickness = 5
);

UPDATE materials
SET price = 2100, status_zakalka = 0
WHERE material_type = 'Зеркало' AND material_variant = 'серебро бесцветное' AND thickness = 5;
