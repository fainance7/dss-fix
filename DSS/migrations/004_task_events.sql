-- =============================================================================
-- DSS — Migration 004: Task Events (история задач, комментарии)
-- Version: 004
-- Date: 2026-03-28
-- =============================================================================

CREATE TABLE IF NOT EXISTS task_events (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id     UUID        NOT NULL REFERENCES regional_tasks(id) ON DELETE CASCADE,
    event_type  VARCHAR(30) NOT NULL,  -- 'status_change' | 'comment' | 'accept' | 'reject_completion'
    old_status  VARCHAR(20),
    new_status  VARCHAR(20),
    comment     TEXT,
    author_id   VARCHAR(36),
    author_name VARCHAR(100),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_task_events_task_id      ON task_events(task_id);
CREATE INDEX IF NOT EXISTS idx_task_events_task_created ON task_events(task_id, created_at);

COMMENT ON TABLE  task_events            IS 'Хронология событий по задаче: смена статуса, комментарии, принятие/отклонение';
COMMENT ON COLUMN task_events.event_type IS 'Тип события: status_change, comment, accept, reject_completion';
