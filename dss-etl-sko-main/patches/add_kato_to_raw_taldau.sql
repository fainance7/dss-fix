ALTER TABLE raw_taldau
    ADD COLUMN IF NOT EXISTS kato_code BIGINT DEFAULT 590000000;

COMMENT ON COLUMN raw_taldau.kato_code IS
    'Код района СКО (terminalId Talдау), для которого запрошены данные.';
