from datetime import date
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class IntakeJsonRow(BaseModel):
    """Одна строка партии при JSON-загрузке.

    Маппинг на DataIntakeRow аналогичен Excel-парсеру:
    indicator_id + region_id указываются явно по UUID, period_* — ISO-даты,
    value — float.
    """

    indicator_id: UUID
    region_id: Optional[UUID] = Field(None, description="Район (для районных данных)")
    station_id: Optional[UUID] = Field(
        None, description="Метеостанция — для декадных данных (period_type=dekad); region_id тогда не нужен"
    )
    period_type: str = Field(..., pattern="^(year|quarter|month|day|dekad)$")
    period_start: date
    period_end: date
    value: float
    source_id: Optional[UUID] = Field(None, description="UUID существующего data_source (опционально)")


class IntakeJsonRequest(BaseModel):
    rows: list[IntakeJsonRow] = Field(..., min_length=1, max_length=10000)
    source_ref: Optional[str] = Field(
        None,
        max_length=500,
        description="Произвольная пометка о происхождении партии (имя файла, имя задачи и т.п.)",
    )
