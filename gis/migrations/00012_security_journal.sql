-- +goose Up
-- Этап 7 (ТЗ п. 4.8.3): неизменяемый журнал событий информационной безопасности.
-- Записи только добавляются; изменение и удаление запрещены триггерами;
-- каждая запись содержит SHA-256 предыдущей (цепочка хешей), поэтому
-- подмена записи обнаруживается командой `gis audit verify`.
CREATE TABLE security_journal (
    id          bigserial   PRIMARY KEY,
    ts          timestamptz NOT NULL DEFAULT now(),
    event       text        NOT NULL,
    remote_addr text        NOT NULL DEFAULT '',
    method      text        NOT NULL DEFAULT '',
    path        text        NOT NULL DEFAULT '',
    status      integer     NOT NULL DEFAULT 0,
    detail      text        NOT NULL DEFAULT '',
    prev_hash   text        NOT NULL,
    hash        text        NOT NULL
);

-- +goose StatementBegin
CREATE FUNCTION security_journal_chain() RETURNS trigger AS $$
DECLARE
    last text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('security_journal'));
    SELECT hash INTO last FROM security_journal ORDER BY id DESC LIMIT 1;
    NEW.ts := now();
    NEW.prev_hash := COALESCE(last, repeat('0', 64));
    NEW.hash := encode(sha256(convert_to(
        NEW.prev_hash || '|' || NEW.id || '|' || to_char(NEW.ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') || '|' ||
        NEW.event || '|' || NEW.remote_addr || '|' || NEW.method || '|' || NEW.path || '|' ||
        NEW.status || '|' || NEW.detail, 'UTF8')), 'hex');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE FUNCTION security_journal_readonly() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'security_journal is append-only (ТЗ 4.8.3)';
END;
$$ LANGUAGE plpgsql;
-- +goose StatementEnd

CREATE TRIGGER security_journal_chain BEFORE INSERT ON security_journal
    FOR EACH ROW EXECUTE FUNCTION security_journal_chain();
CREATE TRIGGER security_journal_no_update BEFORE UPDATE OR DELETE ON security_journal
    FOR EACH ROW EXECUTE FUNCTION security_journal_readonly();
CREATE TRIGGER security_journal_no_truncate BEFORE TRUNCATE ON security_journal
    FOR EACH STATEMENT EXECUTE FUNCTION security_journal_readonly();

-- +goose Down
-- Журнал безопасности не удаляется откатом миграции (ТЗ п. 4.8.3).
SELECT 1;
