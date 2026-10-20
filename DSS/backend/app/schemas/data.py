from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ── Direction ─────────────────────────────────────────────────

class DirectionOut(BaseModel):
    id: UUID
    code: str
    name: str
    description: Optional[str] = None

    model_config = {"from_attributes": True}


# ── Unit ──────────────────────────────────────────────────────

class UnitCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=200)
    is_active: bool = True


class UnitUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=200)
    is_active: Optional[bool] = None


class UnitOut(BaseModel):
    id: UUID
    name: str
    description: Optional[str]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── EconomicSector (справочник секторов экономики) ────────────

class EconomicSectorOut(BaseModel):
    id: UUID
    oked_code: str
    name: str
    short_name: str
    indicator_name: str
    color: str
    is_aggregate: bool
    parent_code: Optional[str]
    sort_order: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class MeteoStationOut(BaseModel):
    id: UUID
    name: str
    region_id: UUID
    kato_code: Optional[str]
    lat: Optional[float]
    lon: Optional[float]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Indicator ─────────────────────────────────────────────────

class IndicatorCreate(BaseModel):
    direction_id: UUID
    unit_id: UUID
    name: str = Field(min_length=1, max_length=300)
    sort_order: int = 0
    is_active: bool = True


class IndicatorUpdate(BaseModel):
    direction_id: Optional[UUID] = None
    unit_id: Optional[UUID] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=300)
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class IndicatorOut(BaseModel):
    id: UUID
    direction_id: UUID
    direction_name: Optional[str] = None
    unit_id: UUID
    unit_name: Optional[str] = None
    name: str
    sort_order: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── IndicatorValue (time-series) ──────────────────────────────

PERIOD_TYPES = ("year", "quarter", "month", "day")


class IndicatorValueCreate(BaseModel):
    indicator_id: UUID
    region_id: UUID
    period_type: str = Field(pattern="^(year|quarter|month|day)$")
    period_start: date
    period_end: date
    value: float
    source_id: Optional[UUID] = None


class IndicatorValueBulkCreate(BaseModel):
    items: list[IndicatorValueCreate]


class IndicatorValueOut(BaseModel):
    id: UUID
    indicator_id: UUID
    indicator_name: Optional[str] = None
    direction_id: Optional[UUID] = None
    region_id: UUID
    region_name: Optional[str] = None
    period_type: str
    period_start: date
    period_end: date
    value: float
    source_id: Optional[UUID] = None
    validation_status: str
    validated_by: Optional[UUID] = None
    validated_at: Optional[datetime] = None
    created_by: Optional[UUID] = None
    created_at: datetime
    # Замечания контроля качества из последней импортированной партии с этим значением.
    quality_flags: list[dict] = []

    model_config = {"from_attributes": True}


class IndicatorValueBulkResult(BaseModel):
    inserted: int
    updated: int
    skipped: int
    errors: list[str]


class BulkValidateRequest(BaseModel):
    ids: Optional[list[UUID]] = None
    job_id: Optional[UUID] = None
    status: str = Field(pattern="^(approved|rejected)$")
    comment: Optional[str] = None


class BulkValidateResult(BaseModel):
    updated: int
    not_found: int


class TimeSeriesPoint(BaseModel):
    period_start: date
    period_end: date
    value: float
    validation_status: str


class IndicatorTimeSeriesOut(BaseModel):
    indicator_id: UUID
    indicator_name: Optional[str] = None
    region_id: UUID
    region_name: Optional[str] = None
    period_type: str
    points: list[TimeSeriesPoint]


# ── IndicatorNorm (среднемноголетняя норма) ───────────────────

class IndicatorNormOut(BaseModel):
    id: UUID
    indicator_id: UUID
    indicator_name: Optional[str] = None
    region_id: UUID
    region_name: Optional[str] = None
    norm_value: float
    period_label: str
    method: str
    ref_period_start: Optional[date] = None
    ref_period_end: Optional[date] = None
    note: Optional[str] = None

    model_config = {"from_attributes": True}


# ── DataSource ────────────────────────────────────────────────

class DataSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    url: Optional[str] = Field(default=None, max_length=500)
    is_active: bool = True


class DataSourceUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    url: Optional[str] = None
    is_active: Optional[bool] = None


class DataSourceOut(BaseModel):
    id: UUID
    name: str
    description: Optional[str]
    url: Optional[str]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── DataCategory ──────────────────────────────────────────────

class DataCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = None
    parent_id: Optional[UUID] = None


class DataCategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = None
    parent_id: Optional[UUID] = None


class DataCategoryOut(BaseModel):
    id: UUID
    name: str
    description: Optional[str]
    parent_id: Optional[UUID]

    model_config = {"from_attributes": True}


# ── DataLayer ─────────────────────────────────────────────────

class DataLayerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    category_id: UUID
    source_id: UUID
    direction_id: UUID
    version: Optional[str] = Field(default=None, max_length=20)
    spatial_resolution: Optional[str] = Field(default=None, max_length=50)
    update_frequency: Optional[str] = Field(default=None, max_length=50)
    owner: Optional[str] = Field(default=None, max_length=200)
    accuracy_description: Optional[str] = None
    valid_from: date
    valid_to: Optional[date] = None
    status: str = Field(default="active", pattern="^(active|archive|pending)$")


class DataLayerUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    direction_id: Optional[UUID] = None
    version: Optional[str] = None
    spatial_resolution: Optional[str] = None
    update_frequency: Optional[str] = None
    owner: Optional[str] = None
    accuracy_description: Optional[str] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    status: Optional[str] = Field(default=None, pattern="^(active|archive|pending)$")


class DataLayerOut(BaseModel):
    id: UUID
    name: str
    description: Optional[str]
    category_id: UUID
    category_name: Optional[str] = None
    source_id: UUID
    source_name: Optional[str] = None
    direction_id: UUID
    direction_name: Optional[str] = None
    version: Optional[str]
    spatial_resolution: Optional[str]
    update_frequency: Optional[str]
    owner: Optional[str]
    accuracy_description: Optional[str]
    valid_from: date
    valid_to: Optional[date]
    status: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DataLayerHistoryOut(BaseModel):
    id: UUID
    layer_id: UUID
    changed_by: UUID
    changed_by_username: Optional[str] = None
    change_type: str
    old_values: Optional[dict]
    new_values: Optional[dict]
    changed_at: datetime

    model_config = {"from_attributes": True}


# ── SpatialData ───────────────────────────────────────────────

class SpatialDataCreate(BaseModel):
    layer_id: UUID
    region_id: Optional[UUID] = None
    data_date: date
    geojson: Optional[dict] = Field(
        default=None,
        description="GeoJSON Geometry объекта (Point/LineString/Polygon/...)",
    )
    storage_path: Optional[str] = Field(default=None, max_length=500)
    properties: Optional[dict] = None


class SpatialDataOut(BaseModel):
    id: UUID
    layer_id: UUID
    layer_name: Optional[str] = None
    region_id: Optional[UUID]
    region_name: Optional[str] = None
    data_date: date
    geojson: Optional[dict] = None
    storage_path: Optional[str]
    properties: Optional[dict]
    validation_status: str
    validated_by: Optional[UUID]
    validated_by_username: Optional[str] = None
    validated_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class SpatialDataFeatureCollection(BaseModel):
    """GeoJSON FeatureCollection response."""
    type: str = "FeatureCollection"
    features: list[dict]
    total: int


class ValidateRequest(BaseModel):
    status: str = Field(pattern="^(approved|rejected)$")
    comment: Optional[str] = None


# ── DataAnnotation ────────────────────────────────────────────

class AnnotationCreate(BaseModel):
    spatial_data_id: Optional[UUID] = None
    layer_id: Optional[UUID] = None
    comment: str = Field(min_length=1)
    point_geojson: Optional[dict] = None


class AnnotationOut(BaseModel):
    id: UUID
    spatial_data_id: Optional[UUID]
    layer_id: Optional[UUID]
    author_id: UUID
    author_username: Optional[str] = None
    comment: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Generic list response ─────────────────────────────────────

class ListResponse(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int
    pages: int


# ── Monitoring ────────────────────────────────────────────────

class SHDMetrics(BaseModel):
    total_sources: int
    active_sources: int
    total_categories: int
    total_layers: int
    active_layers: int
    total_spatial_records: int
    pending_validation: int
    approved_records: int
    rejected_records: int
    total_annotations: int
