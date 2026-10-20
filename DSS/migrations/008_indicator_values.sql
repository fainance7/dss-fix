-- Migration 008: Time-series values of indicators per region
-- Adds indicator_values table with validation workflow.
-- Required for ML training (Prophet/sklearn) and dashboard charts.

BEGIN;

CREATE TABLE IF NOT EXISTS indicator_values (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    indicator_id  UUID NOT NULL REFERENCES indicators(id) ON DELETE CASCADE,
    region_id     UUID NOT NULL REFERENCES regions(id)    ON DELETE RESTRICT,
    period_type   VARCHAR(20)      NOT NULL,
    period_start  DATE             NOT NULL,
    period_end    DATE             NOT NULL,
    value         DOUBLE PRECISION NOT NULL,
    source_id     UUID REFERENCES data_sources(id) ON DELETE SET NULL,

    validation_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    validated_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    validated_at      TIMESTAMPTZ,

    created_by    UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT iv_period_type_chk
        CHECK (period_type IN ('year', 'quarter', 'month', 'day')),
    CONSTRAINT iv_validation_chk
        CHECK (validation_status IN ('pending', 'approved', 'rejected')),
    CONSTRAINT iv_period_order_chk
        CHECK (period_end >= period_start),
    CONSTRAINT iv_unique_value
        UNIQUE (indicator_id, region_id, period_type, period_start)
);

CREATE INDEX IF NOT EXISTS idx_iv_indicator_region
    ON indicator_values (indicator_id, region_id);

CREATE INDEX IF NOT EXISTS idx_iv_period
    ON indicator_values (period_start, period_end);

CREATE INDEX IF NOT EXISTS idx_iv_validation_status
    ON indicator_values (validation_status);

COMMIT;
