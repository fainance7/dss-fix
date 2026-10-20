from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ModelCreate(BaseModel):
    """Схема для регистрации новой модели."""
    name: str = Field(..., min_length=1, max_length=200, description="Название модели")
    version: str = Field(..., min_length=1, max_length=20, description="Версия модели")
    model_type: str = Field(
        ...,
        description="Тип модели: holt_winters | arima | linear | naive | drift | theta",
        pattern="^(holt_winters|arima|linear|naive|drift|theta)$",
    )
    category_id: UUID | None = Field(None, description="ID категории данных")
    parameters: dict | None = Field(default_factory=dict, description="Гиперпараметры модели")
    created_by: UUID = Field(..., description="ID пользователя-создателя")


class ModelResponse(BaseModel):
    """Схема ответа для модели."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    version: str
    model_type: str
    category_id: UUID | None
    # Целевой показатель (у моделей train_global его нет в parameters) — по нему
    # фронт находит рабочую модель показателя для прогноза.
    indicator_id: UUID | None = None
    parameters: dict | None
    metrics: dict | None
    status: str
    trained_at: datetime | None
    created_by: UUID
    created_at: datetime


class ModelMetricsUpdate(BaseModel):
    """Схема для обновления метрик модели."""
    mae: float = Field(..., ge=0.0, description="Mean Absolute Error")
    rmse: float = Field(..., ge=0.0, description="Root Mean Squared Error")
    r2: float = Field(..., le=1.0, description="Coefficient of Determination (R²)")
    mape: float = Field(..., ge=0.0, description="Mean Absolute Percentage Error")
