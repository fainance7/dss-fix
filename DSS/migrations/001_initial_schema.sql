-- =============================================================================
-- DSS (Decision Support System) — Initial Schema Migration
-- Version: 001
-- Date: 2026-03-01
-- СУБД: PostgreSQL 15 + PostGIS 3.3
-- Alembic-compatible pure SQL migration
-- =============================================================================

-- ---------------------------------------------------------------------------
-- РАСШИРЕНИЯ
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;


-- =============================================================================
-- БЛОК 1 — ПОЛЬЗОВАТЕЛИ И БЕЗОПАСНОСТЬ
-- =============================================================================

-- ---------------------------------------------------------------------------
-- regions — иерархия регионов (область → район → населённый пункт)
-- ---------------------------------------------------------------------------
CREATE TABLE regions (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code        VARCHAR(20)  NOT NULL,
    name        VARCHAR(200) NOT NULL,
    parent_id   UUID         REFERENCES regions(id) ON DELETE RESTRICT,
    level       SMALLINT     NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT regions_code_unique UNIQUE (code)
);

COMMENT ON TABLE  regions           IS 'Иерархический справочник регионов (область / район / н.п.)';
COMMENT ON COLUMN regions.level     IS '1 = область, 2 = район, 3 = населённый пункт';
COMMENT ON COLUMN regions.parent_id IS 'Ссылка на родительский регион (NULL для корневых)';


-- ---------------------------------------------------------------------------
-- roles
-- ---------------------------------------------------------------------------
CREATE TABLE roles (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(50)  NOT NULL,
    description TEXT,

    CONSTRAINT roles_name_unique UNIQUE (name)
);

COMMENT ON TABLE roles IS 'Справочник ролей RBAC системы';


-- ---------------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------------
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username        VARCHAR(100) NOT NULL,
    email           VARCHAR(255) NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT users_username_unique UNIQUE (username),
    CONSTRAINT users_email_unique    UNIQUE (email)
);

COMMENT ON TABLE users IS 'Учётные записи пользователей системы';


-- ---------------------------------------------------------------------------
-- user_roles
-- ---------------------------------------------------------------------------
CREATE TABLE user_roles (
    user_id     UUID        NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
    role_id     UUID        NOT NULL REFERENCES roles(id)   ON DELETE RESTRICT,
    -- NULL means the role applies globally (not scoped to a specific region).
    -- region_id excluded from PK because PostgreSQL forbids NULL in PK columns.
    region_id   UUID                 REFERENCES regions(id) ON DELETE SET NULL,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (user_id, role_id)
);

COMMENT ON TABLE  user_roles           IS 'Назначение ролей пользователям (с опциональной привязкой к региону)';
COMMENT ON COLUMN user_roles.region_id IS 'NULL — роль действует на весь уровень доступа пользователя';


-- ---------------------------------------------------------------------------
-- audit_log
-- ---------------------------------------------------------------------------
CREATE TABLE audit_log (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID                 REFERENCES users(id) ON DELETE SET NULL,
    action      VARCHAR(100) NOT NULL,
    entity_type VARCHAR(100) NOT NULL,
    entity_id   UUID,
    details     JSONB,
    ip_address  INET,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE audit_log IS 'Журнал аудита: все значимые действия пользователей';


-- =============================================================================
-- БЛОК 2 — ХРАНЕНИЕ ДАННЫХ
-- =============================================================================

-- ---------------------------------------------------------------------------
-- data_sources
-- ---------------------------------------------------------------------------
CREATE TABLE data_sources (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(200) NOT NULL,
    description TEXT,
    url         VARCHAR(500),
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE data_sources IS 'Реестр источников данных (внешние системы, поставщики)';


-- ---------------------------------------------------------------------------
-- data_categories
-- ---------------------------------------------------------------------------
CREATE TABLE data_categories (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(100) NOT NULL,
    description TEXT,
    parent_id   UUID         REFERENCES data_categories(id) ON DELETE RESTRICT,

    CONSTRAINT data_categories_name_unique UNIQUE (name)
);

COMMENT ON TABLE data_categories IS 'Иерархический классификатор тематических категорий данных';


-- ---------------------------------------------------------------------------
-- data_layers
-- ---------------------------------------------------------------------------
CREATE TABLE data_layers (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                 VARCHAR(200) NOT NULL,
    description          TEXT,
    category_id          UUID         NOT NULL REFERENCES data_categories(id) ON DELETE RESTRICT,
    source_id            UUID         NOT NULL REFERENCES data_sources(id)    ON DELETE RESTRICT,
    version              VARCHAR(20),
    spatial_resolution   VARCHAR(50),
    update_frequency     VARCHAR(50),
    owner                VARCHAR(200),
    accuracy_description TEXT,
    valid_from           DATE         NOT NULL,
    valid_to             DATE,
    status               VARCHAR(20)  NOT NULL DEFAULT 'active',
    created_by           UUID         NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT data_layers_status_check
        CHECK (status IN ('active', 'archive', 'pending'))
);

COMMENT ON TABLE  data_layers        IS 'Метаданные слоёв пространственных и атрибутивных данных';
COMMENT ON COLUMN data_layers.status IS 'active | archive | pending';


-- ---------------------------------------------------------------------------
-- data_layer_history
-- ---------------------------------------------------------------------------
CREATE TABLE data_layer_history (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    layer_id    UUID        NOT NULL REFERENCES data_layers(id) ON DELETE CASCADE,
    changed_by  UUID        NOT NULL REFERENCES users(id)       ON DELETE RESTRICT,
    change_type VARCHAR(50) NOT NULL,
    old_values  JSONB,
    new_values  JSONB,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE data_layer_history IS 'Полная история изменений записей data_layers';


-- ---------------------------------------------------------------------------
-- spatial_data
-- ---------------------------------------------------------------------------
CREATE TABLE spatial_data (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    layer_id          UUID        NOT NULL REFERENCES data_layers(id) ON DELETE RESTRICT,
    region_id         UUID                 REFERENCES regions(id)     ON DELETE SET NULL,
    data_date         DATE        NOT NULL,
    geom              geometry(Geometry, 4326),
    storage_path      VARCHAR(500),
    properties        JSONB,
    validation_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    validated_by      UUID                 REFERENCES users(id)       ON DELETE SET NULL,
    validated_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT spatial_data_validation_status_check
        CHECK (validation_status IN ('pending', 'approved', 'rejected'))
);

COMMENT ON TABLE  spatial_data                   IS 'Пространственные и атрибутивные данные по слоям';
COMMENT ON COLUMN spatial_data.geom              IS 'Геометрия объекта в EPSG:4326 (WGS-84)';
COMMENT ON COLUMN spatial_data.storage_path      IS 'Путь к файлу в объектном хранилище';
COMMENT ON COLUMN spatial_data.validation_status IS 'pending | approved | rejected';


-- ---------------------------------------------------------------------------
-- data_annotations
-- ---------------------------------------------------------------------------
CREATE TABLE data_annotations (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    spatial_data_id  UUID         REFERENCES spatial_data(id)  ON DELETE CASCADE,
    layer_id         UUID         REFERENCES data_layers(id)   ON DELETE CASCADE,
    author_id        UUID NOT NULL REFERENCES users(id)        ON DELETE RESTRICT,
    comment          TEXT NOT NULL,
    annotation_point geometry(Point, 4326),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT data_annotations_target_check
        CHECK (spatial_data_id IS NOT NULL OR layer_id IS NOT NULL)
);

COMMENT ON TABLE data_annotations IS 'Аннотации и комментарии экспертов к пространственным объектам';


-- =============================================================================
-- БЛОК 3 — AI И АНАЛИТИКА
-- =============================================================================

-- ---------------------------------------------------------------------------
-- ai_models
-- ---------------------------------------------------------------------------
CREATE TABLE ai_models (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(200) NOT NULL,
    version     VARCHAR(20)  NOT NULL,
    model_type  VARCHAR(100) NOT NULL,
    category_id UUID                  REFERENCES data_categories(id) ON DELETE SET NULL,
    parameters  JSONB,
    metrics     JSONB,
    status      VARCHAR(20)  NOT NULL DEFAULT 'draft',
    trained_at  TIMESTAMPTZ,
    created_by  UUID         NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT ai_models_status_check
        CHECK (status IN ('draft', 'training', 'active', 'deprecated'))
);

COMMENT ON TABLE  ai_models        IS 'Реестр AI/ML моделей с версионированием и метриками';
COMMENT ON COLUMN ai_models.status IS 'draft | training | active | deprecated';


-- ---------------------------------------------------------------------------
-- forecasts
-- ---------------------------------------------------------------------------
CREATE TABLE forecasts (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    model_id          UUID        NOT NULL REFERENCES ai_models(id) ON DELETE RESTRICT,
    region_id         UUID                 REFERENCES regions(id)   ON DELETE SET NULL,
    forecast_date     DATE        NOT NULL,
    target_date_from  DATE        NOT NULL,
    target_date_to    DATE        NOT NULL,
    result_values     JSONB,
    confidence_scores JSONB,
    xai_explanation   JSONB,
    status            VARCHAR(20) NOT NULL DEFAULT 'pending',
    triggered_by      UUID                 REFERENCES users(id)    ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT forecasts_target_dates_check
        CHECK (target_date_to >= target_date_from)
);

COMMENT ON TABLE  forecasts             IS 'Результаты прогнозирования AI-моделей';
COMMENT ON COLUMN forecasts.xai_explanation IS 'Объяснение прогноза (XAI): важность факторов';


-- ---------------------------------------------------------------------------
-- calculated_indicators
-- ---------------------------------------------------------------------------
CREATE TABLE calculated_indicators (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                VARCHAR(200) NOT NULL,
    formula_description TEXT,
    category_id         UUID              REFERENCES data_categories(id) ON DELETE SET NULL,
    region_id           UUID              REFERENCES regions(id)         ON DELETE SET NULL,
    indicator_date      DATE        NOT NULL,
    value               NUMERIC(20, 6) NOT NULL,
    unit                VARCHAR(50),
    parameters          JSONB,
    calculated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE calculated_indicators IS 'Агрегированные и производные показатели (КПИ, индексы)';


-- =============================================================================
-- БЛОК 4 — СЦЕНАРИИ И ЗАДАЧИ
-- =============================================================================

-- ---------------------------------------------------------------------------
-- development_scenarios
-- ---------------------------------------------------------------------------
CREATE TABLE development_scenarios (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name          VARCHAR(200) NOT NULL,
    scenario_type VARCHAR(20)  NOT NULL,
    target_year   SMALLINT     NOT NULL,
    region_id     UUID         NOT NULL REFERENCES regions(id) ON DELETE RESTRICT,
    parameters    JSONB,
    status        VARCHAR(20)  NOT NULL DEFAULT 'draft',
    approved_by   UUID                  REFERENCES users(id)   ON DELETE SET NULL,
    approved_at   TIMESTAMPTZ,
    created_by    UUID         NOT NULL REFERENCES users(id)   ON DELETE RESTRICT,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT development_scenarios_type_check
        CHECK (scenario_type IN ('optimal', 'stable', 'risk', 'custom')),
    CONSTRAINT development_scenarios_status_check
        CHECK (status IN ('draft', 'calculated', 'approved', 'rejected')),
    CONSTRAINT development_scenarios_target_year_check
        CHECK (target_year BETWEEN 2020 AND 2100)
);

COMMENT ON TABLE  development_scenarios               IS 'Сценарии развития регионов';
COMMENT ON COLUMN development_scenarios.scenario_type IS 'optimal | stable | risk | custom';
COMMENT ON COLUMN development_scenarios.status        IS 'draft | calculated | approved | rejected';


-- ---------------------------------------------------------------------------
-- scenario_comparisons
-- ---------------------------------------------------------------------------
CREATE TABLE scenario_comparisons (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name              VARCHAR(200) NOT NULL,
    scenario_ids      UUID[]       NOT NULL,
    comparison_matrix JSONB,
    created_by        UUID         NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE scenario_comparisons IS 'Сохранённые матрицы сравнения нескольких сценариев';


-- ---------------------------------------------------------------------------
-- regional_tasks
-- ---------------------------------------------------------------------------
CREATE TABLE regional_tasks (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    scenario_id       UUID         NOT NULL REFERENCES development_scenarios(id) ON DELETE RESTRICT,
    region_id         UUID         NOT NULL REFERENCES regions(id)               ON DELETE RESTRICT,
    title             VARCHAR(300) NOT NULL,
    description       TEXT,
    category          VARCHAR(100),
    target_metric     VARCHAR(200),
    target_value      NUMERIC(20, 6),
    unit              VARCHAR(50),
    deadline          DATE,
    status            VARCHAR(20)  NOT NULL DEFAULT 'pending',
    assigned_to       UUID                  REFERENCES users(id)                 ON DELETE SET NULL,
    completion_report TEXT,
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT regional_tasks_status_check
        CHECK (status IN ('pending', 'in_progress', 'completed', 'cancelled'))
);

COMMENT ON TABLE  regional_tasks        IS 'Задачи из утверждённого сценария для исполнителей района';
COMMENT ON COLUMN regional_tasks.status IS 'pending | in_progress | completed | cancelled';


-- =============================================================================
-- ИНДЕКСЫ
-- =============================================================================

-- regions
CREATE INDEX idx_regions_parent_id ON regions (parent_id);
CREATE INDEX idx_regions_level     ON regions (level);

-- user_roles
CREATE UNIQUE INDEX idx_user_roles_null_region
    ON user_roles (user_id, role_id)
    WHERE region_id IS NULL;

CREATE INDEX idx_user_roles_user_id   ON user_roles (user_id);
CREATE INDEX idx_user_roles_role_id   ON user_roles (role_id);
CREATE INDEX idx_user_roles_region_id ON user_roles (region_id);

-- audit_log
CREATE INDEX idx_audit_log_user_id    ON audit_log (user_id);
CREATE INDEX idx_audit_log_created_at ON audit_log (created_at DESC);
CREATE INDEX idx_audit_log_entity     ON audit_log (entity_type, entity_id);

-- data_layers
CREATE INDEX idx_data_layers_category_id ON data_layers (category_id);
CREATE INDEX idx_data_layers_source_id   ON data_layers (source_id);
CREATE INDEX idx_data_layers_created_by  ON data_layers (created_by);
CREATE INDEX idx_data_layers_status      ON data_layers (status);

-- data_layer_history
CREATE INDEX idx_dlh_layer_id   ON data_layer_history (layer_id);
CREATE INDEX idx_dlh_changed_by ON data_layer_history (changed_by);
CREATE INDEX idx_dlh_changed_at ON data_layer_history (changed_at DESC);

-- spatial_data — GIST на геометрию (обязательно для пространственных запросов)
CREATE INDEX idx_spatial_data_geom
    ON spatial_data USING GIST (geom);

-- Составной B-tree: основной паттерн доступа layer_id + data_date
CREATE INDEX idx_spatial_data_layer_date
    ON spatial_data (layer_id, data_date);

CREATE INDEX idx_spatial_data_region_id         ON spatial_data (region_id);
CREATE INDEX idx_spatial_data_validated_by      ON spatial_data (validated_by);
CREATE INDEX idx_spatial_data_validation_status ON spatial_data (validation_status);

-- data_annotations
CREATE INDEX idx_data_annotations_spatial_data_id ON data_annotations (spatial_data_id);
CREATE INDEX idx_data_annotations_layer_id        ON data_annotations (layer_id);
CREATE INDEX idx_data_annotations_author_id       ON data_annotations (author_id);
CREATE INDEX idx_data_annotations_point
    ON data_annotations USING GIST (annotation_point);

-- ai_models
CREATE INDEX idx_ai_models_category_id ON ai_models (category_id);
CREATE INDEX idx_ai_models_created_by  ON ai_models (created_by);
CREATE INDEX idx_ai_models_status      ON ai_models (status);

-- forecasts
CREATE INDEX idx_forecasts_model_id      ON forecasts (model_id);
CREATE INDEX idx_forecasts_region_id     ON forecasts (region_id);
CREATE INDEX idx_forecasts_forecast_date ON forecasts (forecast_date DESC);
CREATE INDEX idx_forecasts_triggered_by  ON forecasts (triggered_by);

-- calculated_indicators
CREATE INDEX idx_calc_ind_category_id    ON calculated_indicators (category_id);
CREATE INDEX idx_calc_ind_region_id      ON calculated_indicators (region_id);
CREATE INDEX idx_calc_ind_indicator_date ON calculated_indicators (indicator_date DESC);

-- development_scenarios
CREATE INDEX idx_dev_scenarios_region_id   ON development_scenarios (region_id);
CREATE INDEX idx_dev_scenarios_created_by  ON development_scenarios (created_by);
CREATE INDEX idx_dev_scenarios_approved_by ON development_scenarios (approved_by);
CREATE INDEX idx_dev_scenarios_status      ON development_scenarios (status);
CREATE INDEX idx_dev_scenarios_target_year ON development_scenarios (target_year);

-- regional_tasks
CREATE INDEX idx_regional_tasks_scenario_id ON regional_tasks (scenario_id);
CREATE INDEX idx_regional_tasks_region_id   ON regional_tasks (region_id);
CREATE INDEX idx_regional_tasks_assigned_to ON regional_tasks (assigned_to);
CREATE INDEX idx_regional_tasks_status      ON regional_tasks (status);
CREATE INDEX idx_regional_tasks_deadline    ON regional_tasks (deadline);


-- =============================================================================
-- НАЧАЛЬНЫЕ ДАННЫЕ — РОЛИ
-- =============================================================================

INSERT INTO roles (id, name, description) VALUES
    (uuid_generate_v4(), 'admin',
     'Системный администратор: полный доступ, управление пользователями, мониторинг'),
    (uuid_generate_v4(), 'exp_ver',
     'Эксперт-верификатор: загрузка данных, валидация, аннотирование слоёв'),
    (uuid_generate_v4(), 'exp_dir',
     'Эксперт-директор: управление AI-моделями, запуск прогнозов, подготовка сценариев'),
    (uuid_generate_v4(), 'akim_obl',
     'Аким области: аналитика, утверждение/отклонение сценариев развития'),
    (uuid_generate_v4(), 'akim_ray',
     'Аким района: задачи по сценарию, обновление статусов и отчётность');


-- =============================================================================
-- ТРИГГЕРЫ — автообновление updated_at
-- =============================================================================

CREATE OR REPLACE FUNCTION trg_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

CREATE TRIGGER trg_data_layers_updated_at
    BEFORE UPDATE ON data_layers
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

CREATE TRIGGER trg_development_scenarios_updated_at
    BEFORE UPDATE ON development_scenarios
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

CREATE TRIGGER trg_regional_tasks_updated_at
    BEFORE UPDATE ON regional_tasks
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();


-- =============================================================================
-- END OF MIGRATION 001