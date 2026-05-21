-- Расширение заявок: доставка, длинный service_type, тип файла.
ALTER TABLE blocks_zamer ADD COLUMN IF NOT EXISTS is_delivery BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE blocks_zamer_file ADD COLUMN IF NOT EXISTS file_kind VARCHAR(24) NOT NULL DEFAULT 'measure';
ALTER TABLE blocks_zamer ALTER COLUMN service_type TYPE VARCHAR(32);
ALTER TABLE blocks_zamer ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMPTZ NULL;
