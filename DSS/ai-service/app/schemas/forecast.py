from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ForecastCreate(BaseModel):
    """Запрос на создание и запуск прогноза.

    Горизонт задаётся в ГОДАХ (`horizon_years`): исторические ряды DSS годовые.
    `horizon_months` сохранён для обратной совместимости и трактуется как число
    годовых шагов — именно так он и работал фактически.
    """
    model_id: UUID
    indicator_id: Optional[UUID] = None
    region_id: Optional[UUID] = None
    horizon_years: Optional[int] = Field(default=None, ge=1, le=25)
    horizon_months: Optional[int] = Field(default=None, ge=1, le=120, deprecated=True)

    @property
    def effective_horizon_years(self) -> Optional[int]:
        return self.horizon_years if self.horizon_years is not None else self.horizon_months


class ForecastResultItem(BaseModel):
    """Одна точка прогноза для построения графика."""
    date: str
    value: float
    low: float
    high: float


class ForecastResponse(BaseModel):
    """Полный ответ с данными прогноза."""
    id: UUID
    model_id: UUID
    indicator_id: Optional[UUID]
    region_id: Optional[UUID]
    forecast_date: date
    target_date_from: date
    target_date_to: date
    result_values: Optional[dict[str, Any]]
    confidence_scores: Optional[dict[str, Any]]
    xai_explanation: Optional[dict[str, Any]]
    status: str
    triggered_by: Optional[UUID]
    created_at: datetime

    model_config = {"from_attributes": True}


class ForecastListResponse(BaseModel):
    """Список прогнозов с пагинацией."""
    items: list[ForecastResponse]
    total: int
    page: int
    page_size: int
    pages: int
