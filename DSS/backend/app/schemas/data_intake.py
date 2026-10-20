from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class IntakeRowOut(BaseModel):
    id: UUID
    row_no: int
    status: str
    indicator_id: Optional[UUID] = None
    indicator_name: Optional[str] = None
    region_id: Optional[UUID] = None
    region_name: Optional[str] = None
    period_type: Optional[str] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    value: Optional[float] = None
    error_message: Optional[str] = None
    raw_payload: dict[str, Any]

    model_config = {"from_attributes": True}


class IntakeJobOut(BaseModel):
    id: UUID
    source_kind: str
    source_ref: str
    uploaded_by: Optional[UUID] = None
    uploaded_at: datetime
    status: str
    rows_total: int
    rows_ok: int
    rows_invalid: int
    rows_imported: int
    data_source_id: Optional[UUID] = None
    committed_at: Optional[datetime] = None
    committed_by: Optional[UUID] = None
    validation_summary: Optional[dict[str, Any]] = None
    quality_summary: Optional[dict[str, Any]] = None

    model_config = {"from_attributes": True}


class IntakeJobDetailOut(IntakeJobOut):
    rows: list[IntakeRowOut] = Field(default_factory=list)
    value_stats: dict[str, int] = Field(default_factory=dict)


class IntakeUploadResponse(BaseModel):
    job: IntakeJobOut
    rows: list[IntakeRowOut]
    errors_total: int


class IntakeJobListResponse(BaseModel):
    items: list[IntakeJobOut]
    total: int
    page: int
    page_size: int
    pages: int


class IntakeCommitResult(BaseModel):
    job_id: UUID
    inserted_or_updated: int
    skipped: int
    errors: list[str]


class IntakeRevalidateResult(BaseModel):
    job: IntakeJobOut
    rechecked: int
    became_ok: int
    still_invalid: int


class IntakePreviewItem(BaseModel):
    row_no: int
    indicator_name: Optional[str] = None
    region_name: Optional[str] = None
    period_type: Optional[str] = None
    period_start: Optional[date] = None
    change_type: str                      # "new" | "changed" | "unchanged"
    old_value: Optional[float] = None      # текущее значение в indicator_values (None для new)
    new_value: Optional[float] = None      # значение из партии
    resets_approved: bool = False          # существующая запись сейчас approved → commit сбросит её


class IntakePreviewResult(BaseModel):
    job_id: UUID
    total: int
    new_count: int
    changed_count: int
    unchanged_count: int
    resets_approved_count: int
    items: list[IntakePreviewItem] = Field(default_factory=list)  # детализация: new + changed


class IntakeCommitRequest(BaseModel):
    """Подтверждение импорта партии с замечаниями уровня «блокер»."""
    acknowledge: bool = False
    comment: Optional[str] = Field(default=None, max_length=2000)
