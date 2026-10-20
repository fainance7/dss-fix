-- Migration 037: версия токенов пользователя (аудит безопасности, M-7).
--
-- Каждый JWT несёт версию токенов пользователя (tv). Смена пароля, выход,
-- блокировка и изменение ролей увеличивают users.token_version, после чего
-- ранее выданные access- и refresh-токены отклоняются.

ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version integer NOT NULL DEFAULT 0;
