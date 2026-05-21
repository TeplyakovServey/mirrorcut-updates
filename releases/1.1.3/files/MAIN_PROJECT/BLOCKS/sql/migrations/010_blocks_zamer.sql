-- Замеры для BLOCKS / портал монтажников.
CREATE TABLE IF NOT EXISTS blocks_zamer (
    id BIGSERIAL PRIMARY KEY,
    client_id INTEGER NULL,
    address TEXT NOT NULL DEFAULT '',
    date_from DATE NULL,
    date_to DATE NULL,
    phone TEXT NOT NULL DEFAULT '',
    matches_client BOOLEAN NOT NULL DEFAULT FALSE,
    extra_text TEXT NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL DEFAULT 'new',
    agreed_at TIMESTAMPTZ NULL,
    comment_manager TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS blocks_zamer_file (
    id BIGSERIAL PRIMARY KEY,
    zamer_id BIGINT NOT NULL REFERENCES blocks_zamer(id) ON DELETE CASCADE,
    file_url TEXT NOT NULL DEFAULT '',
    comment TEXT NOT NULL DEFAULT '',
    uploaded_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_blocks_zamer_status ON blocks_zamer(status);
CREATE INDEX IF NOT EXISTS idx_blocks_zamer_file_zamer ON blocks_zamer_file(zamer_id);
