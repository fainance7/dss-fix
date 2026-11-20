-- +goose Up
-- Landscape Program: seed the category and dataset groups (input slots and
-- node outputs; codes must match internal/landscape/graph.go), and store runs.
INSERT INTO categories (code, name, description)
VALUES ('landscape', 'Ландшафтная программа', 'Входные и результирующие растры Ландшафтной программы')
ON CONFLICT (code) DO NOTHING;

INSERT INTO dataset_groups (code, name) VALUES
    ('lp-z-biodiversity', 'Z Biodiversity'), ('lp-z-lcr', 'Z LCR'), ('lp-z-soil-protection', 'Z Soil Protection'), ('lp-z-wr', 'Z Water Resources'),
    ('lp-u-surf-water', 'U Surface Water'), ('lp-u-soil', 'U Soil'), ('lp-u-crop', 'U Crop'), ('lp-u-veg', 'U Vegetation'),
    ('lp-p-irrigation', 'P Irrigation'), ('lp-p-snow', 'P Snow'), ('lp-p-crop', 'P Crop'), ('lp-p-pasture', 'P Pasture'), ('lp-p-tourism', 'P Tourism'),
    ('lp-z-integrated', 'Z Integrated'), ('lp-u-integrated', 'U Integrated'), ('lp-z-max', 'Z Max'), ('lp-u-max', 'U Max'),
    ('lp-e', 'E Ecological Constraint'),
    ('lp-c-irrigation', 'C Irrigation'), ('lp-c-snow', 'C Snow'), ('lp-c-crop', 'C Crop'), ('lp-c-pasture', 'C Pasture'), ('lp-c-tourism', 'C Tourism'),
    ('lp-p-max', 'P Max'),
    ('lp-v1-conservative', 'V1 Conservative'), ('lp-v2-balanced', 'V2 Balanced'), ('lp-v3-development', 'V3 Development'), ('lp-c-max', 'C Max')
ON CONFLICT (code) DO NOTHING;

CREATE TABLE lp_runs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status      VARCHAR(32) NOT NULL,
    from_stage  SMALLINT,
    params      JSONB NOT NULL,
    overrides   JSONB NOT NULL DEFAULT '{}'::jsonb,
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE INDEX idx_lp_runs_created_at ON lp_runs (created_at DESC);

CREATE TABLE lp_run_nodes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES lp_runs (id) ON DELETE CASCADE,
    node_code       VARCHAR(8) NOT NULL,
    stage           SMALLINT NOT NULL,
    status          VARCHAR(32) NOT NULL,
    fingerprint     TEXT NOT NULL DEFAULT '',
    input_datasets  JSONB NOT NULL DEFAULT '[]'::jsonb,
    output_datasets JSONB,
    error           TEXT,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    UNIQUE (run_id, node_code)
);
CREATE INDEX idx_lp_run_nodes_lookup ON lp_run_nodes (node_code, fingerprint, status, finished_at DESC);

-- +goose Down
DROP TABLE lp_run_nodes;
DROP TABLE lp_runs;
-- Seeded category/groups are left in place (datasets may reference them).
