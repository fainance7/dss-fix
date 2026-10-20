-- 029_landscape_impact.sql
-- Прогоны алгоритма ЗБ-УР: оценка влияния земельного баланса на устойчивое развитие.
--
-- Модуль отвечает на вопрос заказчика: как изменение земельного баланса (категории
-- земель, угодья, землепользование) влияет на региональную статистику и через неё
-- на индексы устойчивого развития; какой из вариантов ландшафтного планирования
-- выбрать. Алгоритм описан в docs/Алгоритм_ЗБ_УР_2026-09-09.md.
--
-- Проектное правило: опубликованный прогон неизменяем. Пересчёт создаёт новую
-- строку, а не переписывает старую: решения по зонированию имеют юридические
-- последствия, и история оценок должна сохраняться.

CREATE TABLE IF NOT EXISTS landscape_impact_runs (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                VARCHAR(200) NOT NULL,
    variant_code        VARCHAR(50),                -- lp-v1-conservative | lp-v2-balanced | lp-v3-development
    region_id           UUID,                       -- FK regions.id (DB-level)
    base_year           SMALLINT NOT NULL,
    horizon_years       SMALLINT NOT NULL,

    -- Вход расчёта: целевые площади категорий либо доли классов растра ЛП.
    scenario_input      JSONB NOT NULL,

    -- Полный результат прогона: калибровка, матрица переходов, распределения,
    -- гейты, вердикт и трассировка каждого числа.
    result              JSONB,

    status              VARCHAR(20) NOT NULL DEFAULT 'pending',
    gate_status         VARCHAR(30),                -- PUBLISH | PUBLISH_WITH_WARNING | RESEARCH_ONLY
    verdict             VARCHAR(20),                -- supports | hinders | inconclusive
    error               TEXT,

    -- Версии всего, что влияет на число (гейт G12 «воспроизводимость»).
    algorithm_version   VARCHAR(20) NOT NULL,
    assumptions_version VARCHAR(20) NOT NULL,
    datamart_version    VARCHAR(20) NOT NULL,
    random_seed         INTEGER NOT NULL,
    draws               INTEGER NOT NULL,

    created_by          UUID,                       -- FK users.id (DB-level)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ,

    CONSTRAINT landscape_impact_runs_status_check
        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    CONSTRAINT landscape_impact_runs_verdict_check
        CHECK (verdict IS NULL OR verdict IN ('supports', 'hinders', 'inconclusive'))
);

CREATE INDEX IF NOT EXISTS idx_li_runs_created_at ON landscape_impact_runs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_li_runs_status     ON landscape_impact_runs (status);
CREATE INDEX IF NOT EXISTS idx_li_runs_variant    ON landscape_impact_runs (variant_code);

COMMENT ON TABLE landscape_impact_runs IS
    'Прогоны алгоритма ЗБ-УР (земельный баланс → устойчивое развитие). Опубликованный прогон неизменяем.';
COMMENT ON COLUMN landscape_impact_runs.gate_status IS
    'Статус гейтов G1-G13. RESEARCH_ONLY означает, что прогон не допущен на дашборд.';
COMMENT ON COLUMN landscape_impact_runs.result IS
    'Полный результат, включая трассировку меток типа связи [Б]/[С]/[Э] по каждому числу.';
