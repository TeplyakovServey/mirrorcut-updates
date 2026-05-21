-- Событийное обновление десктопа: pg_notify при изменении замеров / файлов (LISTEN zamer_board).
-- Не HTTP-поллинг: уведомление приходит сразу после COMMIT транзакции на портале.

CREATE OR REPLACE FUNCTION mc_notify_zamer_board() RETURNS TRIGGER AS $$
BEGIN
  PERFORM pg_notify('zamer_board', '');
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_blocks_zamer_notify ON blocks_zamer;
CREATE TRIGGER trg_blocks_zamer_notify
  AFTER INSERT OR UPDATE OR DELETE ON blocks_zamer
  FOR EACH ROW
  EXECUTE PROCEDURE mc_notify_zamer_board();

DROP TRIGGER IF EXISTS trg_blocks_zamer_file_notify ON blocks_zamer_file;
CREATE TRIGGER trg_blocks_zamer_file_notify
  AFTER INSERT OR UPDATE OR DELETE ON blocks_zamer_file
  FOR EACH ROW
  EXECUTE PROCEDURE mc_notify_zamer_board();
