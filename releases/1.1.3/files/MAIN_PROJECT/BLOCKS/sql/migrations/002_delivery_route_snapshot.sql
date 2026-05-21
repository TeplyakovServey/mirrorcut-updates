-- Снимок последнего маршрута доставки (вне КАД) для повторной загрузки в UI.
CREATE TABLE IF NOT EXISTS delivery_route_snapshot (
    route_key VARCHAR(32) PRIMARY KEY,
    payload_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
