"""Схемы связей каталога GIS (dssgis.dwh.kz) с доменом DSS.

Категория GIS ↔ направление (Direction), датасет GIS ↔ показатель (Indicator).
Upsert-схемы принимают внешний GIS-идентификатор + целевой объект DSS; Out-схемы
отдают связь с кэшированными именами для отображения.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


# ── Категория GIS → направление DSS ───────────────────────────

class GisCategoryLinkUpsert(BaseModel):
    gis_category_id: str
    direction_id: UUID
    gis_category_code: Optional[str] = None
    gis_category_name: Optional[str] = None


class GisCategoryLinkOut(BaseModel):
    id: UUID
    gis_category_id: str
    gis_category_code: Optional[str] = None
    gis_category_name: Optional[str] = None
    direction_id: UUID
    direction_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Датасет GIS → показатель DSS ──────────────────────────────

class GisDatasetLinkUpsert(BaseModel):
    gis_dataset_id: str
    indicator_id: UUID
    gis_dataset_name: Optional[str] = None
    gis_category_id: Optional[str] = None


class GisDatasetLinkOut(BaseModel):
    id: UUID
    gis_dataset_id: str
    gis_dataset_name: Optional[str] = None
    gis_category_id: Optional[str] = None
    indicator_id: UUID
    indicator_name: Optional[str] = None
    direction_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
