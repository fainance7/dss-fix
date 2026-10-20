-- +goose Up
CREATE TABLE categories (
    id          UUID PRIMARY KEY      DEFAULT gen_random_uuid(),
    parent_id   UUID                  REFERENCES categories (id) ON DELETE RESTRICT,
    -- Slug: lowercase latin letters, digits, and dashes. Unique.
    code        VARCHAR(255) NOT NULL  UNIQUE,
    name        VARCHAR(255) NOT NULL,
    description TEXT         NOT NULL  DEFAULT '',
    created_at  TIMESTAMPTZ  NOT NULL  DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL  DEFAULT now()
);

CREATE INDEX idx_categories_parent_id ON categories (parent_id);

-- +goose Down
DROP TABLE categories;
