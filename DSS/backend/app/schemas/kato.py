from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class KatoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kato_code:    str
    name_ru:      str
    name_kz:      str | None
    type_code:    str | None
    type_name_ru: str | None
    parent_code:  str | None
    level:        int
    is_active:    bool
    region_id:    UUID | None
    created_at:   datetime


class KatoListResponse(BaseModel):
    items: list[KatoResponse]
    total: int


class KatoCreate(BaseModel):
    kato_code:    str           = Field(min_length=1, max_length=9)
    name_ru:      str           = Field(min_length=1, max_length=250)
    name_kz:      Optional[str] = Field(default=None, max_length=250)
    type_code:    Optional[str] = Field(default=None, max_length=10)
    type_name_ru: Optional[str] = Field(default=None, max_length=100)
    parent_code:  Optional[str] = Field(default=None, max_length=9)
    level:        int           = Field(ge=1, le=5)
    is_active:    bool          = True
    region_id:    Optional[UUID] = None


class KatoUpdate(BaseModel):
    name_ru:      Optional[str]  = Field(default=None, min_length=1, max_length=250)
    name_kz:      Optional[str]  = None
    type_code:    Optional[str]  = Field(default=None, max_length=10)
    type_name_ru: Optional[str]  = Field(default=None, max_length=100)
    parent_code:  Optional[str]  = Field(default=None, max_length=9)
    level:        Optional[int]  = Field(default=None, ge=1, le=5)
    is_active:    Optional[bool] = None
    region_id:    Optional[UUID] = None


class KatoImportStatus(BaseModel):
    status:        str            # "running" | "done" | "error"
    total:         int
    processed:     int
    added:         int
    updated:       int
    skipped:       int
    errors:        list[str]
    error_message: Optional[str] = None
