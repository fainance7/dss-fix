-- +goose Up
CREATE TYPE file_type AS ENUM ('vector_with_kato', 'vector', 'raster');

CREATE TABLE datasets (
    id           UUID PRIMARY KEY       DEFAULT gen_random_uuid(),
    category_id  UUID         NOT NULL  REFERENCES categories (id) ON DELETE RESTRICT,
    name         VARCHAR(255) NOT NULL,
    description  TEXT,
    unit         VARCHAR(255),
    filename     VARCHAR(255) NOT NULL,
    storage_key  TEXT         NOT NULL,
    cog_storage_key TEXT,                -- Cloud-Optimized GeoTIFF (raster only)
    file_type    file_type    NOT NULL,
    size_bytes   BIGINT       NOT NULL  DEFAULT 0,
    content_type VARCHAR(255) NOT NULL  DEFAULT '',
    properties   JSONB,
    meta         JSONB,
    automated    BOOLEAN      NOT NULL  DEFAULT false,
    status       VARCHAR(255) NOT NULL  DEFAULT 'pending',
    -- vector_with_kato attribute-table parsing + mapping
    attribute_columns JSONB,            -- detected columns: [{name, samples}]
    kato_column       VARCHAR(255),     -- user-selected KATO column
    year_columns      JSONB,            -- mapping: [{column, date}]
    parse_error       TEXT,             -- failure reason when status = 'failed'
    -- Footprint/extent in EPSG:4326 (any geometry type). The SRID is constrained
    -- so geometry and the derived bbox are always comparable.
    geometry     geometry(Geometry, 4326),
    created_at   TIMESTAMPTZ  NOT NULL  DEFAULT now(),
    updated_at   TIMESTAMPTZ  NOT NULL  DEFAULT now()
);

CREATE INDEX idx_datasets_category_id ON datasets (category_id);
CREATE INDEX idx_datasets_geometry ON datasets USING GIST (geometry);
CREATE INDEX idx_datasets_created_at ON datasets (created_at DESC);

-- +goose Down
DROP TABLE datasets;
DROP TYPE file_type;
