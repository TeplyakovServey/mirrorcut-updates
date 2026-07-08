-- Событийное обновление главной таблицы заказов: pg_notify → LISTEN mirror_orders_list.
-- Payload: mo:insert|update|delete:id, ms:..., cl:id, pe:op:order_id, bz:order_id, refresh

CREATE OR REPLACE FUNCTION mc_notify_mirror_orders_list() RETURNS TRIGGER AS $$
DECLARE
  payload text;
  oid bigint;
BEGIN
  IF TG_TABLE_NAME = 'mirror_orders' THEN
    IF TG_OP = 'DELETE' THEN
      payload := 'mo:delete:' || OLD.id;
    ELSIF TG_OP = 'INSERT' THEN
      payload := 'mo:insert:' || NEW.id;
    ELSE
      payload := 'mo:update:' || NEW.id;
    END IF;
  ELSIF TG_TABLE_NAME = 'mirror_sales_orders' THEN
    IF TG_OP = 'DELETE' THEN
      payload := 'ms:delete:' || OLD.id;
    ELSIF TG_OP = 'INSERT' THEN
      payload := 'ms:insert:' || NEW.id;
    ELSE
      payload := 'ms:update:' || NEW.id;
    END IF;
  ELSIF TG_TABLE_NAME = 'mirror_clients' THEN
    IF TG_OP = 'DELETE' THEN
      payload := 'cl:delete:' || OLD.id;
    ELSIF TG_OP = 'INSERT' THEN
      payload := 'cl:insert:' || NEW.id;
    ELSE
      payload := 'cl:update:' || NEW.id;
    END IF;
  ELSIF TG_TABLE_NAME = 'mirror_production_events' THEN
    oid := COALESCE(NEW.order_id, OLD.order_id);
    payload := 'pe:' || lower(TG_OP) || ':' || oid;
  ELSIF TG_TABLE_NAME = 'blocks_zamer' THEN
    oid := COALESCE(NEW.mirror_order_id, OLD.mirror_order_id);
    payload := 'bz:' || oid;
  ELSIF TG_TABLE_NAME = 'blocks_zamer_file' THEN
    SELECT z.mirror_order_id INTO oid
    FROM blocks_zamer z
    WHERE z.id = COALESCE(NEW.zamer_id, OLD.zamer_id);
    payload := 'bz:' || COALESCE(oid, 0);
  ELSE
    payload := 'refresh';
  END IF;
  PERFORM pg_notify('mirror_orders_list', payload);
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_mirror_orders_list_notify ON mirror_orders;
CREATE TRIGGER trg_mirror_orders_list_notify
  AFTER INSERT OR UPDATE OR DELETE ON mirror_orders
  FOR EACH ROW
  EXECUTE PROCEDURE mc_notify_mirror_orders_list();

DROP TRIGGER IF EXISTS trg_mirror_sales_orders_list_notify ON mirror_sales_orders;
CREATE TRIGGER trg_mirror_sales_orders_list_notify
  AFTER INSERT OR UPDATE OR DELETE ON mirror_sales_orders
  FOR EACH ROW
  EXECUTE PROCEDURE mc_notify_mirror_orders_list();

DROP TRIGGER IF EXISTS trg_mirror_clients_list_notify ON mirror_clients;
CREATE TRIGGER trg_mirror_clients_list_notify
  AFTER INSERT OR UPDATE OR DELETE ON mirror_clients
  FOR EACH ROW
  EXECUTE PROCEDURE mc_notify_mirror_orders_list();

DROP TRIGGER IF EXISTS trg_mirror_production_events_list_notify ON mirror_production_events;
CREATE TRIGGER trg_mirror_production_events_list_notify
  AFTER INSERT OR UPDATE OR DELETE ON mirror_production_events
  FOR EACH ROW
  EXECUTE PROCEDURE mc_notify_mirror_orders_list();

DROP TRIGGER IF EXISTS trg_blocks_zamer_orders_list_notify ON blocks_zamer;
CREATE TRIGGER trg_blocks_zamer_orders_list_notify
  AFTER INSERT OR UPDATE OR DELETE ON blocks_zamer
  FOR EACH ROW
  EXECUTE PROCEDURE mc_notify_mirror_orders_list();

DROP TRIGGER IF EXISTS trg_blocks_zamer_file_orders_list_notify ON blocks_zamer_file;
CREATE TRIGGER trg_blocks_zamer_file_orders_list_notify
  AFTER INSERT OR UPDATE OR DELETE ON blocks_zamer_file
  FOR EACH ROW
  EXECUTE PROCEDURE mc_notify_mirror_orders_list();
