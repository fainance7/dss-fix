-- Migration 030: dashboard summaries (краткие выводы ИИ на вкладках дашбордов)
-- Вывод генерируется локальной LLM один раз и хранится здесь; перегенерация —
-- только когда меняется отпечаток данных вкладки (fingerprint), т.е. при
-- появлении новых значений показателей.
-- Идемпотентно (IF NOT EXISTS) — применяется на каждом деплое.

BEGIN;

CREATE TABLE IF NOT EXISTS dashboard_summaries (
    page_key       TEXT PRIMARY KEY,
    direction_code TEXT NOT NULL,
    title          TEXT NOT NULL,
    fingerprint    TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'running',
    summary        TEXT,
    source         TEXT,
    model          TEXT,
    error          TEXT,
    generated_at   TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE dashboard_summaries IS
    'Краткие выводы ИИ по вкладкам дашбордов направлений. page_key — «страница:вкладка»; '
    'fingerprint — отпечаток фактов вкладки (значения показателей, приведённые к отчётному году); '
    'status: running | done; source: llm | template (запасной шаблонный текст при недоступности LLM).';

COMMIT;
