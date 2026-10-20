from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class PeriodColumn(BaseModel):
    index: int = Field(ge=0, description="0-based индекс колонки в remote-таблице")
    marker: str = Field(min_length=1, max_length=10, description="'01'..'12' для monthly, '1'..'4' для quarterly")


class IntakeMappingBase(BaseModel):
    remote_table: str = Field(min_length=1, max_length=200)
    indicator_id: UUID
    region_resolution_kind: str = Field(pattern="^(constant|by_name)$")
    kato_constant: Optional[str] = Field(default=None, max_length=9)
    region_column_idx: Optional[int] = Field(default=None, ge=0)
    region_name_to_kato: Optional[dict[str, str]] = None
    period_kind: str = Field(pattern="^(yearly|quarterly|monthly|daily)$")
    year_column_idx: Optional[int] = Field(default=None, ge=0)
    period_columns_idx: list[PeriodColumn]
    skip_if_first_col_in: list[str] = Field(default_factory=list)
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    source_description: Optional[str] = None
    notes: Optional[str] = None


class IntakeMappingCreate(IntakeMappingBase):
    pass


class IntakeMappingUpdate(BaseModel):
    indicator_id: Optional[UUID] = None
    region_resolution_kind: Optional[str] = Field(default=None, pattern="^(constant|by_name)$")
    kato_constant: Optional[str] = Field(default=None, max_length=9)
    region_column_idx: Optional[int] = Field(default=None, ge=0)
    region_name_to_kato: Optional[dict[str, str]] = None
    period_kind: Optional[str] = Field(default=None, pattern="^(yearly|quarterly|monthly|daily)$")
    year_column_idx: Optional[int] = Field(default=None, ge=0)
    period_columns_idx: Optional[list[PeriodColumn]] = None
    skip_if_first_col_in: Optional[list[str]] = None
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    source_description: Optional[str] = None
    notes: Optional[str] = None


class IntakeMappingOut(IntakeMappingBase):
    id: UUID
    indicator_name: Optional[str] = None
    created_by: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[str] = None
    last_run_summary: Optional[dict[str, Any]] = None

    model_config = {"from_attributes": True}
