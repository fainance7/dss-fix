-- +goose Up
-- Pre-assembled GeoJSON FeatureCollection served by the /datasets/{id}.geojson
-- endpoint. Generated at processing time for vector and vector_with_kato
-- datasets (NULL until generated / for rasters).
ALTER TABLE datasets ADD COLUMN geojson JSONB;

-- +goose Down
ALTER TABLE datasets DROP COLUMN geojson;
