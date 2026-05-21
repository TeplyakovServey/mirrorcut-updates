-- Портал монтажников: кто оформил замер с веба, общий комментарий уже в comment_manager.
ALTER TABLE blocks_zamer ADD COLUMN IF NOT EXISTS montazh_author_name TEXT NOT NULL DEFAULT '';
