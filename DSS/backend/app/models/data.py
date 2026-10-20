import uuid
from datetime import date, datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, Float, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base


class Direction(Base):
    __tablename__ = "directions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), nullable=False, unique=True)
    name = Column(String(200), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    data_layers = relationship("DataLayer", back_populates="direction")
    indicators = relationship(
        "Indicator", back_populates="direction", cascade="all, delete-orphan"
    )


class Unit(Base):
    __tablename__ = "units"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(String(200), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    indicators = relationship("Indicator", back_populates="unit")


class EconomicSector(Base):
    """Справочник секторов экономики (ОКЭД, разделы A–S) для отображения на
    дашбордах. См. migrations/016_economic_sectors.sql."""

    __tablename__ = "economic_sectors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    oked_code = Column(String(8), nullable=False)
    name = Column(String(300), nullable=False, unique=True)
    short_name = Column(String(80), nullable=False)
    indicator_name = Column(String(300), nullable=False, unique=True)
    color = Column(String(7), nullable=False)
    is_aggregate = Column(Boolean, nullable=False, default=False)
    parent_code = Column(String(8), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class MeteoStation(Base):
    """Справочник метеостанций СКО: станция → район (+ координаты WGS84)."""
    __tablename__ = "meteo_stations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(120), nullable=False, unique=True)
    region_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    kato_code = Column(String(9), nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ClimateRegionStationMap(Base):
    """Какие станции дают значение района: свои (own) или ближайшая (nearest)."""
    __tablename__ = "climate_region_station_map"

    region_id = Column(UUID(as_uuid=True), ForeignKey("regions.id", ondelete="CASCADE"), primary_key=True)
    station_id = Column(UUID(as_uuid=True), ForeignKey("meteo_stations.id", ondelete="CASCADE"), primary_key=True)
    mode = Column(String(10), nullable=False)

    __table_args__ = (CheckConstraint("mode IN ('own', 'nearest')", name="crsm_mode_chk"),)


class MeteoStationValue(Base):
    """Значение показателя по метеостанции: декада (первичное) или месяц (расчётный).

    Месяц станции считает app/services/climate_dekads.py из трёх декад.
    """
    __tablename__ = "meteo_station_values"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    station_id = Column(UUID(as_uuid=True), ForeignKey("meteo_stations.id", ondelete="RESTRICT"), nullable=False)
    indicator_id = Column(UUID(as_uuid=True), ForeignKey("indicators.id", ondelete="CASCADE"), nullable=False)
    period_type = Column(String(10), nullable=False)  # 'dekad' | 'month'
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    dekad = Column(Integer, nullable=True)  # 1..3 для декады
    value = Column(Float, nullable=False)
    is_derived = Column(Boolean, nullable=False, default=False)
    derivation = Column(String(30), nullable=True)  # 'month_from_dekads' | 'd1_reconstructed'
    source_id = Column(UUID(as_uuid=True), ForeignKey("data_sources.id", ondelete="SET NULL"), nullable=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("dataset_intake_jobs.id", ondelete="SET NULL"), nullable=True)
    validation_status = Column(String(20), nullable=False, default="pending")
    validated_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    validated_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("station_id", "indicator_id", "period_type", "period_start", name="msv_unique"),
        CheckConstraint("validation_status IN ('pending', 'approved', 'rejected')", name="msv_status_chk"),
        CheckConstraint(
            "(period_type = 'dekad' AND dekad BETWEEN 1 AND 3"
            " AND EXTRACT(DAY FROM period_start) = CASE dekad WHEN 1 THEN 1 WHEN 2 THEN 11 ELSE 21 END"
            " AND period_end = CASE WHEN dekad < 3 THEN period_start + 9"
            " ELSE (date_trunc('month', period_start) + interval '1 month - 1 day')::date END)"
            " OR (period_type = 'month' AND dekad IS NULL AND EXTRACT(DAY FROM period_start) = 1"
            " AND period_end = (date_trunc('month', period_start) + interval '1 month - 1 day')::date)",
            name="msv_shape_chk",
        ),
        Index("idx_msv_ind_period", "indicator_id", "period_type", "period_start"),
        Index("idx_msv_station", "station_id"),
        Index("idx_msv_status", "validation_status"),
    )


class Indicator(Base):
    __tablename__ = "indicators"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    direction_id = Column(
        UUID(as_uuid=True),
        ForeignKey("directions.id", ondelete="CASCADE"),
        nullable=False,
    )
    unit_id = Column(
        UUID(as_uuid=True),
        ForeignKey("units.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name = Column(String(300), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    # Периодичность ряда: 'year' (по умолчанию) или 'month' — подгодовые ряды
    # не должны попадать в годовые выборки (прогнозы, годовые дашборды).
    period_kind = Column(String(10), nullable=False, default="year", server_default="year")
    # Значения считает Система (например, месяц района из декад станций);
    # ручная запись через приём данных и CRUD запрещена.
    is_computed = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    direction = relationship("Direction", back_populates="indicators")
    unit = relationship("Unit", back_populates="indicators")
    values = relationship(
        "IndicatorValue", back_populates="indicator", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("direction_id", "name", name="indicators_direction_name_unique"),
        Index("idx_indicators_direction", "direction_id"),
        Index("idx_indicators_unit", "unit_id"),
    )


class IndicatorValue(Base):
    __tablename__ = "indicator_values"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    indicator_id = Column(
        UUID(as_uuid=True),
        ForeignKey("indicators.id", ondelete="CASCADE"),
        nullable=False,
    )
    region_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    period_type = Column(String(20), nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    value = Column(Float, nullable=False)
    source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="SET NULL"),
        nullable=True,
    )

    validation_status = Column(String(20), nullable=False, default="pending")
    validated_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    validated_at = Column(DateTime(timezone=True), nullable=True)

    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    indicator = relationship("Indicator", back_populates="values")
    region = relationship("Region", foreign_keys=[region_id])
    source = relationship("DataSource", foreign_keys=[source_id])
    validated_by_user = relationship("User", foreign_keys=[validated_by])
    created_by_user = relationship("User", foreign_keys=[created_by])

    __table_args__ = (
        CheckConstraint(
            "period_type <> 'month' OR (EXTRACT(DAY FROM period_start) = 1 AND period_end = "
            "(date_trunc('month', period_start) + interval '1 month - 1 day')::date)",
            name="iv_month_shape_chk",
        ),
        UniqueConstraint(
            "indicator_id", "region_id", "period_type", "period_start",
            name="iv_unique_value",
        ),
        CheckConstraint(
            "period_type IN ('year', 'quarter', 'month', 'day')",
            name="iv_period_type_chk",
        ),
        CheckConstraint(
            "validation_status IN ('pending', 'approved', 'rejected')",
            name="iv_validation_chk",
        ),
        CheckConstraint("period_end >= period_start", name="iv_period_order_chk"),
        Index("idx_iv_indicator_region", "indicator_id", "region_id"),
        Index("idx_iv_period", "period_start", "period_end"),
        Index("idx_iv_validation_status", "validation_status"),
    )


class IndicatorValueArchive(Base):
    """Заменённые значения показателей (миграция 035): прежний ряд сохраняется для сравнения."""
    __tablename__ = "indicator_values_archive"

    id = Column(UUID(as_uuid=True), primary_key=True)
    archived_at = Column(DateTime(timezone=True), primary_key=True, server_default=func.now())
    indicator_id = Column(UUID(as_uuid=True), nullable=False)
    region_id = Column(UUID(as_uuid=True), nullable=False)
    period_type = Column(String(20), nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    value = Column(Float, nullable=False)
    source_id = Column(UUID(as_uuid=True), nullable=True)
    validation_status = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)
    reason = Column(Text, nullable=False)


class IndicatorNorm(Base):
    """Среднемноголетняя норма показателя — ОДНА константа на (показатель, район).

    Отдельно от indicator_values (это не временной ряд и без validation-workflow).
    Источник — ручной CC-столбец метеоданных (+ производные за весну/осень).
    См. migrations/021_indicator_norms.sql и scripts/import_climate_norms.py.
    """

    __tablename__ = "indicator_norms"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    indicator_id = Column(
        UUID(as_uuid=True),
        ForeignKey("indicators.id", ondelete="CASCADE"),
        nullable=False,
    )
    region_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    norm_value = Column(Float, nullable=False)
    period_label = Column(String(60), nullable=False, default="multiyear")
    method = Column(String(30), nullable=False, default="manual_cc")
    ref_period_start = Column(Date, nullable=True)
    ref_period_end = Column(Date, nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    indicator = relationship("Indicator")
    region = relationship("Region", foreign_keys=[region_id])

    __table_args__ = (
        UniqueConstraint(
            "indicator_id", "region_id", "period_label",
            name="indicator_norms_unique",
        ),
        Index("idx_indicator_norms_indicator", "indicator_id"),
        Index("idx_indicator_norms_region", "indicator_id", "region_id"),
    )


class DataSource(Base):
    __tablename__ = "data_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    url = Column(String(500), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    data_layers = relationship("DataLayer", back_populates="source")


class DataCategory(Base):
    __tablename__ = "data_categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    parent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("data_categories.id", ondelete="RESTRICT"),
        nullable=True,
    )

    parent = relationship("DataCategory", remote_side=[id], backref="children")
    data_layers = relationship("DataLayer", back_populates="category")


class DataLayer(Base):
    __tablename__ = "data_layers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    category_id = Column(
        UUID(as_uuid=True),
        ForeignKey("data_categories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version = Column(String(20), nullable=True)
    spatial_resolution = Column(String(50), nullable=True)
    update_frequency = Column(String(50), nullable=True)
    owner = Column(String(200), nullable=True)
    accuracy_description = Column(Text, nullable=True)
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)
    direction_id = Column(
        UUID(as_uuid=True),
        ForeignKey("directions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status = Column(String(20), nullable=False, default="active")
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    category = relationship("DataCategory", back_populates="data_layers")
    source = relationship("DataSource", back_populates="data_layers")
    direction = relationship("Direction", back_populates="data_layers")
    created_by_user = relationship("User", foreign_keys=[created_by])
    history = relationship(
        "DataLayerHistory",
        back_populates="layer",
        cascade="all, delete-orphan",
    )
    spatial_data = relationship("SpatialData", back_populates="layer")
    annotations = relationship("DataAnnotation", back_populates="layer")

    __table_args__ = (
        Index("idx_data_layers_category_id", "category_id"),
        Index("idx_data_layers_source_id", "source_id"),
        Index("idx_data_layers_direction_id", "direction_id"),
        Index("idx_data_layers_status", "status"),
    )


class DataLayerHistory(Base):
    __tablename__ = "data_layer_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    layer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("data_layers.id", ondelete="CASCADE"),
        nullable=False,
    )
    changed_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    change_type = Column(String(50), nullable=False)
    old_values = Column(JSONB, nullable=True)
    new_values = Column(JSONB, nullable=True)
    changed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    layer = relationship("DataLayer", back_populates="history")
    changed_by_user = relationship("User", foreign_keys=[changed_by])

    __table_args__ = (
        Index("idx_dlh_layer_id", "layer_id"),
        Index("idx_dlh_changed_at", "changed_at"),
    )


class SpatialData(Base):
    __tablename__ = "spatial_data"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    layer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("data_layers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    region_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regions.id", ondelete="SET NULL"),
        nullable=True,
    )
    data_date = Column(Date, nullable=False)
    geom = Column(Geometry(geometry_type="GEOMETRY", srid=4326), nullable=True)
    storage_path = Column(String(500), nullable=True)
    properties = Column(JSONB, nullable=True)
    validation_status = Column(String(20), nullable=False, default="pending")
    validated_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    validated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    layer = relationship("DataLayer", back_populates="spatial_data")
    region = relationship("Region", foreign_keys=[region_id])
    validated_by_user = relationship("User", foreign_keys=[validated_by])
    annotations = relationship("DataAnnotation", back_populates="spatial_data_obj")

    __table_args__ = (
        Index("idx_spatial_data_layer_date", "layer_id", "data_date"),
        Index("idx_spatial_data_region_id", "region_id"),
        Index("idx_spatial_data_validation_status", "validation_status"),
    )


class DataAnnotation(Base):
    __tablename__ = "data_annotations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    spatial_data_id = Column(
        UUID(as_uuid=True),
        ForeignKey("spatial_data.id", ondelete="CASCADE"),
        nullable=True,
    )
    layer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("data_layers.id", ondelete="CASCADE"),
        nullable=True,
    )
    author_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Аннотация к значению показателя в таблице (ФFE.1) — без точки на карте.
    indicator_value_id = Column(
        UUID(as_uuid=True),
        ForeignKey("indicator_values.id", ondelete="CASCADE"),
        nullable=True,
    )
    comment = Column(Text, nullable=False)
    annotation_point = Column(Geometry(geometry_type="POINT", srid=4326), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    spatial_data_obj = relationship("SpatialData", back_populates="annotations")
    layer = relationship("DataLayer", back_populates="annotations")
    author = relationship("User", foreign_keys=[author_id])

    __table_args__ = (
        Index("idx_data_annotations_spatial_data_id", "spatial_data_id"),
        Index("idx_data_annotations_layer_id", "layer_id"),
        Index("idx_data_annotations_author_id", "author_id"),
        Index("idx_data_annotations_indicator_value_id", "indicator_value_id"),
    )


class GisCategoryLink(Base):
    """Связь категории GIS (dssgis.dwh.kz) с направлением DSS. См.
    migrations/025_gis_links.sql. Внешний UUID категории уникален (одна категория
    → одно направление); одно направление может иметь несколько категорий."""

    __tablename__ = "gis_category_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    gis_category_id = Column(String, nullable=False, unique=True)
    gis_category_code = Column(String, nullable=True)
    gis_category_name = Column(String, nullable=True)
    direction_id = Column(
        UUID(as_uuid=True),
        ForeignKey("directions.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    direction = relationship("Direction")

    __table_args__ = (Index("idx_gis_category_links_direction", "direction_id"),)


class GisDatasetLink(Base):
    """Связь датасета GIS (dssgis.dwh.kz) с показателем DSS. См.
    migrations/025_gis_links.sql. Внешний UUID датасета уникален (один датасет →
    один показатель); один показатель может иметь несколько датасетов (напр. по годам)."""

    __tablename__ = "gis_dataset_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    gis_dataset_id = Column(String, nullable=False, unique=True)
    gis_dataset_name = Column(String, nullable=True)
    gis_category_id = Column(String, nullable=True)
    indicator_id = Column(
        UUID(as_uuid=True),
        ForeignKey("indicators.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    indicator = relationship("Indicator")

    __table_args__ = (
        Index("idx_gis_dataset_links_indicator", "indicator_id"),
        Index("idx_gis_dataset_links_gis_category", "gis_category_id"),
    )
