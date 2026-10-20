-- +goose Up
-- Administrative districts, keyed by KATO code. Geometry is the district
-- boundary stored as MultiPolygon in EPSG:4326. Populated from a districts
-- GeoJSON file via the `gis import-districts` subcommand.
CREATE TABLE districts (
    id          UUID PRIMARY KEY  DEFAULT gen_random_uuid(),
    kato        TEXT NOT NULL,
    name        TEXT NOT NULL,
    coordinates geometry(MultiPolygon, 4326) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_districts_kato ON districts (kato);
CREATE INDEX idx_districts_coordinates ON districts USING GIST (coordinates);

-- +goose Down
DROP TABLE districts;
