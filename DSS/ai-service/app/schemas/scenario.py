from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ScenarioParameters(BaseModel):
    """
    Параметры-рычаги для расчёта сценария развития.
    Все значения — множители или целевые показатели.
    """
    # Мультипликатор инвестиций (0.5 = сокращение вдвое, 2.0 = удвоение)
    investment_multiplier: float = Field(default=1.0, ge=0.5, le=2.0)
    # Темп прироста населения в год (-0.05 = убыль 5%, +0.05 = рост 5%)
    population_growth_rate: float = Field(default=0.01, ge=-0.05, le=0.05)
    # Индекс развития инфраструктуры (0.5 = деградация, 2.0 = опережающее развитие)
    infrastructure_index: float = Field(default=1.0, ge=0.5, le=2.0)
    # Целевой уровень занятости (0.70–1.0)
    employment_target: float = Field(default=0.95, ge=0.7, le=1.0)
    # Целевой темп роста ВРП в год (-0.10 = спад 10%, +0.20 = рост 20%)
    gdp_growth_target: float = Field(default=0.03, ge=-0.1, le=0.2)


class ScenarioCreate(BaseModel):
    """Запрос на создание нового сценария развития."""
    name: str = Field(min_length=1, max_length=200)
    scenario_type: str = Field(pattern="^(optimal|stable|risk|custom)$")
    target_year: int = Field(ge=2025, le=2050)
    region_id: UUID
    parameters: ScenarioParameters = Field(default_factory=ScenarioParameters)
    created_by: UUID


class ScenarioResponse(BaseModel):
    """Полный ответ со сценарием и вычисленными KPI."""
    id: UUID
    name: str
    scenario_type: str
    target_year: int
    region_id: UUID
    # parameters содержит исходные рычаги + computed_kpis после расчёта
    parameters: Optional[dict[str, Any]]
    status: str
    narrative: Optional[str] = None
    narrative_generated_at: Optional[datetime] = None
    narrative_status: Optional[str] = None
    narrative_error: Optional[str] = None
    # Нарративы по направлениям: {direction_code: {text, status, generated_at, error}}.
    narratives: dict[str, Any] = Field(default_factory=dict)
    approved_by: Optional[UUID]
    approved_at: Optional[datetime]
    created_by: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class GenerateNarrativeRequest(BaseModel):
    """Запрос на генерацию нарратива сценария локальной автономной LLM.

    `force=true` перегенерирует нарратив, даже если сохранённый актуален
    (отпечаток входных данных совпадает).
    """
    direction_code: str = Field(min_length=1, max_length=50)
    force: bool = False


class GenerateAllNarrativesRequest(BaseModel):
    """Запрос на генерацию нарративов сразу по нескольким направлениям.
    Обрабатываются ПОСЛЕДОВАТЕЛЬНО в одной фоновой задаче (на CPU параллелизм
    не ускоряет). `force=False` — пропускать направления, где нарратив уже готов."""
    directions: list[str] = Field(min_length=1)
    force: bool = False


class NarrativeResponse(BaseModel):
    scenario_id: UUID
    narrative: str
    generated_at: datetime
    model: str
    elapsed_seconds: float


class NarrativeStatusResponse(BaseModel):
    """Статус асинхронной генерации нарратива (для старта и поллинга).

    Всегда относится к конкретному направлению (`direction_code`); нарративы
    разных направлений хранятся и опрашиваются независимо.
    """
    scenario_id: UUID
    direction_code: Optional[str] = None
    narrative_status: str  # idle | running | done | failed
    narrative: Optional[str] = None
    narrative_generated_at: Optional[datetime] = None
    narrative_error: Optional[str] = None
    model: Optional[str] = None


class ScenarioCompare(BaseModel):
    """Сравнение нескольких сценариев."""
    scenarios: list[ScenarioResponse]
    total: int


class GenerateScenariosRequest(BaseModel):
    """Запрос на генерацию всех 3 стандартных сценариев для региона."""
    region_id: UUID
    target_year: int = Field(ge=2025, le=2050)
    created_by: UUID
