"""
SQLAlchemy 2.0 async ORM models for the AI microservice.

Note: Cross-service FK columns (users.id, regions.id, data_categories.id) are
declared as plain UUID without ORM-level ForeignKey to avoid metadata resolution
issues across microservice boundaries. The actual DB constraints are enforced
via the shared migration schema.
"""
import uuid

from sqlalchemy import (
    Column, Date, DateTime, ForeignKey, Index, Integer, LargeBinary,
    Numeric, SmallInteger, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base


class AIModel(Base):
    """Реестр AI/ML моделей с версионированием и метриками."""
    __tablename__ = "ai_models"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    version = Column(String(20), nullable=False)
    model_type = Column(String(100), nullable=False)
    indicator_id = Column(UUID(as_uuid=True), nullable=True)  # FK indicators.id (DB-level): целевой показатель модели
    category_id = Column(UUID(as_uuid=True), nullable=True)   # FK data_categories.id (DB-level)
    parameters = Column(JSONB, nullable=True)
    metrics = Column(JSONB, nullable=True)
    model_blob = Column(LargeBinary, nullable=True)  # сериализованный артефакт обученной модели
    status = Column(String(20), nullable=False, default="draft")
    trained_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(UUID(as_uuid=True), nullable=False)   # FK users.id (DB-level)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    forecasts = relationship("Forecast", back_populates="model")

    __table_args__ = (
        Index("idx_ai_models_model_type_status", "model_type", "status"),
        Index("idx_ai_models_created_by", "created_by"),
    )


class Forecast(Base):
    """Результаты прогнозирования AI-моделей."""
    __tablename__ = "forecasts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_models.id", ondelete="RESTRICT"),
        nullable=False,
    )
    indicator_id = Column(UUID(as_uuid=True), nullable=True)  # FK indicators.id (DB-level): прогнозируемый показатель
    region_id = Column(UUID(as_uuid=True), nullable=True)     # FK regions.id (DB-level)
    forecast_date = Column(Date, nullable=False)
    target_date_from = Column(Date, nullable=False)
    target_date_to = Column(Date, nullable=False)
    result_values = Column(JSONB, nullable=True)
    confidence_scores = Column(JSONB, nullable=True)
    xai_explanation = Column(JSONB, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    triggered_by = Column(UUID(as_uuid=True), nullable=True)  # FK users.id (DB-level)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    model = relationship("AIModel", back_populates="forecasts")

    __table_args__ = (
        Index("idx_forecasts_model_id", "model_id"),
        Index("idx_forecasts_region_id", "region_id"),
        Index("idx_forecasts_forecast_date", "forecast_date"),
    )


class DevelopmentScenario(Base):
    """Сценарии развития регионов (оптимальный / стабильный / рисковый)."""
    __tablename__ = "development_scenarios"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    scenario_type = Column(String(20), nullable=False)
    target_year = Column(SmallInteger, nullable=False)
    region_id = Column(UUID(as_uuid=True), nullable=False)    # FK regions.id (DB-level)
    parameters = Column(JSONB, nullable=True)
    status = Column(String(20), nullable=False, default="draft")
    # Легаси-поля — отражают ПОСЛЕДНЮЮ генерацию (обратная совместимость).
    narrative = Column(Text, nullable=True)
    narrative_generated_at = Column(DateTime(timezone=True), nullable=True)
    narrative_status = Column(String(20), nullable=True)  # pending|running|done|failed
    narrative_error = Column(Text, nullable=True)
    # Нарративы по направлениям: {direction_code: {text, status, generated_at, error}}.
    narratives = Column(JSONB, nullable=False, server_default="{}", default=dict)
    approved_by = Column(UUID(as_uuid=True), nullable=True)   # FK users.id (DB-level)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(UUID(as_uuid=True), nullable=False)   # FK users.id (DB-level)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    tasks = relationship("RegionalTask", back_populates="scenario")

    __table_args__ = (
        Index("idx_dev_scenarios_region_id", "region_id"),
        Index("idx_dev_scenarios_status", "status"),
        Index("idx_dev_scenarios_type", "scenario_type"),
    )


class RegionalTask(Base):
    """Задачи из утверждённого сценария для исполнителей района."""
    __tablename__ = "regional_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scenario_id = Column(
        UUID(as_uuid=True),
        ForeignKey("development_scenarios.id", ondelete="RESTRICT"),
        nullable=False,
    )
    region_id = Column(UUID(as_uuid=True), nullable=False)    # FK regions.id (DB-level)
    title = Column(String(300), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(100), nullable=True)
    target_metric = Column(String(200), nullable=True)
    target_value = Column(Numeric(20, 6), nullable=True)
    unit = Column(String(50), nullable=True)
    deadline = Column(Date, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    assigned_to = Column(UUID(as_uuid=True), nullable=True)   # FK users.id (DB-level)
    completion_report = Column(Text, nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    scenario = relationship("DevelopmentScenario", back_populates="tasks")
    events = relationship("TaskEvent", back_populates="task", order_by="TaskEvent.created_at")

    __table_args__ = (
        Index("idx_regional_tasks_scenario_id", "scenario_id"),
        Index("idx_regional_tasks_region_id", "region_id"),
        Index("idx_regional_tasks_status", "status"),
        Index("idx_regional_tasks_assigned_to", "assigned_to"),
    )


class TaskEvent(Base):
    """История изменений и комментарии к задачам."""
    __tablename__ = "task_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regional_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type = Column(String(30), nullable=False)   # comment | status_change | accept | reject_completion
    old_status = Column(String(20), nullable=True)
    new_status = Column(String(20), nullable=True)
    comment = Column(Text, nullable=True)
    author_id = Column(String(36), nullable=True)
    author_name = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    task = relationship("RegionalTask", back_populates="events")


class CalculatedIndicator(Base):
    """Агрегированные и производные показатели (КПИ, индексы)."""
    __tablename__ = "calculated_indicators"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    formula_description = Column(Text, nullable=True)
    category_id = Column(UUID(as_uuid=True), nullable=True)   # FK data_categories.id (DB-level)
    region_id = Column(UUID(as_uuid=True), nullable=True)     # FK regions.id (DB-level)
    indicator_date = Column(Date, nullable=False)
    value = Column(Numeric(20, 6), nullable=False)
    unit = Column(String(50), nullable=True)
    parameters = Column(JSONB, nullable=True)
    calculated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_calc_ind_region_id", "region_id"),
        Index("idx_calc_ind_indicator_date", "indicator_date"),
    )


class LandscapeImpactRun(Base):
    """Прогон алгоритма ЗБ-УР: земельный баланс → устойчивое развитие.

    Опубликованный прогон неизменяем: пересчёт создаёт новую строку. Решения по
    зонированию имеют юридические последствия, поэтому история оценок с версиями
    входных данных и коэффициентов должна сохраняться целиком (гейт G12).
    """
    __tablename__ = "landscape_impact_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    variant_code = Column(String(50), nullable=True)
    region_id = Column(UUID(as_uuid=True), nullable=True)     # FK regions.id (DB-level)
    base_year = Column(SmallInteger, nullable=False)
    horizon_years = Column(SmallInteger, nullable=False)

    scenario_input = Column(JSONB, nullable=False)
    result = Column(JSONB, nullable=True)

    status = Column(String(20), nullable=False, default="pending")
    gate_status = Column(String(30), nullable=True)
    verdict = Column(String(20), nullable=True)
    error = Column(Text, nullable=True)

    algorithm_version = Column(String(20), nullable=False)
    assumptions_version = Column(String(20), nullable=False)
    datamart_version = Column(String(20), nullable=False)
    random_seed = Column(Integer, nullable=False)
    draws = Column(Integer, nullable=False)

    created_by = Column(UUID(as_uuid=True), nullable=True)    # FK users.id (DB-level)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_li_runs_created_at", "created_at"),
        Index("idx_li_runs_status", "status"),
        Index("idx_li_runs_variant", "variant_code"),
    )
