-- +goose Up
-- Sink for the generic example RabbitMQ consumer. Safe to drop once a real
-- async use case replaces the scaffold.
CREATE TABLE events (
    id         UUID PRIMARY KEY      DEFAULT gen_random_uuid(),
    kind       VARCHAR(255) NOT NULL,
    payload    JSONB        NOT NULL  DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ  NOT NULL  DEFAULT now()
);

-- +goose Down
DROP TABLE events;
