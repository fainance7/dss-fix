-- Migration 014: API-ключи для неинтерактивной авторизации (intake portal).
-- Plain-ключ отдаётся клиенту только один раз при создании; в БД храним SHA-256 хэш.
-- Используется в sub-app /intake-portal через подмену get_current_active_user.
-- Также расширяет CHECK на dataset_intake_jobs.source_kind, чтобы разрешить 'json_api'.

BEGIN;

CREATE TABLE IF NOT EXISTS api_keys (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash      VARCHAR(128) NOT NULL UNIQUE,
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name          VARCHAR(100) NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    expires_at    TIMESTAMPTZ,
    last_used_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by    UUID REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);

-- Разрешаем source_kind='json_api' для партий, созданных через intake portal.
ALTER TABLE dataset_intake_jobs DROP CONSTRAINT IF EXISTS dij_source_kind_chk;
ALTER TABLE dataset_intake_jobs
    ADD CONSTRAINT dij_source_kind_chk
    CHECK (source_kind IN ('excel_template', 'remote_reports_db', 'json_api'));

COMMIT;
