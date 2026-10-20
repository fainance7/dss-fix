-- +goose Up
-- Enable PostGIS so datasets can carry a spatial geometry column.
CREATE EXTENSION IF NOT EXISTS postgis;

-- +goose Down
DROP EXTENSION IF EXISTS postgis;
