-- Migration 002 — Add IIN field to users table
-- ИИН (Индивидуальный идентификационный номер) — 12 цифр, уникальный идентификатор гражданина РК.
-- Nullable: существующие пользователи сохраняются без ИИН.
-- Используется для входа по ЭЦП (NCALayer).
-- Idempotent: safe to run multiple times.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'iin'
    ) THEN
        ALTER TABLE users ADD COLUMN iin VARCHAR(12) NULL;
        ALTER TABLE users ADD CONSTRAINT users_iin_unique UNIQUE (iin);
        ALTER TABLE users ADD CONSTRAINT users_iin_format
            CHECK (iin IS NULL OR iin ~ '^\d{12}$');
        COMMENT ON COLUMN users.iin IS 'ИИН/БИН — 12 цифр, для входа по ЭЦП';
    END IF;
END $$;
