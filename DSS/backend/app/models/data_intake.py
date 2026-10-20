import uuid

from sqlalchemy import (
    CheckConstraint, Column, Date, DateTime, Float, ForeignKey,
    Index, Integer, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base


class DataIntakeJob(Base):
    __tablename__ = "dataset_intake_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_kind = Column(String(30), nullable=False)
    source_ref = Column(Text, nullable=False)
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    uploaded_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    status = Column(String(20), nullable=False, default="pending")
    rows_total = Column(Integer, nullable=False, default=0)
    rows_ok = Column(Integer, nullable=False, default=0)
    rows_invalid = Column(Integer, nullable=False, default=0)
    rows_imported = Column(Integer, nullable=False, default=0)
    validation_summary = Column(JSONB, nullable=True)
    # Отчёт контроля качества (app/services/intake_quality.py), снимок до импорта.
    quality_report = Column(JSONB, nullable=True)
    data_source_id = Column(UUID(as_uuid=True), ForeignKey("data_sources.id", ondelete="SET NULL"), nullable=True)
    committed_at = Column(DateTime(timezone=True), nullable=True)
    committed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    rows = relationship("DataIntakeRow", back_populates="job", cascade="all, delete-orphan")

    @property
    def quality_summary(self) -> dict | None:
        """Краткая сводка контроля качества для списка партий."""
        rep = self.quality_report or {}
        if not rep:
            return None
        return {**(rep.get("summary") or {}), "acknowledged": bool(rep.get("acknowledged"))}
    uploader = relationship("User", foreign_keys=[uploaded_by])
    committer = relationship("User", foreign_keys=[committed_by])
    data_source = relationship("DataSource", foreign_keys=[data_source_id])

    __table_args__ = (
        CheckConstraint(
            "source_kind IN ('excel_template', 'remote_reports_db', 'json_api')",
            name="dij_source_kind_chk",
        ),
        CheckConstraint(
            "status IN ('pending', 'validated', 'imported', 'failed', 'cancelled')",
            name="dij_status_chk",
        ),
        Index("idx_dij_status", "status"),
        Index("idx_dij_uploaded_by", "uploaded_by"),
        Index("idx_dij_uploaded_at", "uploaded_at"),
    )


class DataIntakeRow(Base):
    __tablename__ = "dataset_intake_rows"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("dataset_intake_jobs.id", ondelete="CASCADE"), nullable=False)
    row_no = Column(Integer, nullable=False)
    raw_payload = Column(JSONB, nullable=False)
    indicator_id = Column(UUID(as_uuid=True), ForeignKey("indicators.id", ondelete="SET NULL"), nullable=True)
    region_id = Column(UUID(as_uuid=True), ForeignKey("regions.id", ondelete="SET NULL"), nullable=True)
    # Для декадных строк по метеостанциям (period_type='dekad')
    station_id = Column(UUID(as_uuid=True), ForeignKey("meteo_stations.id", ondelete="SET NULL"), nullable=True)
    period_type = Column(String(20), nullable=True)
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=True)
    value = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="invalid")

    job = relationship("DataIntakeJob", back_populates="rows")
    indicator = relationship("Indicator", foreign_keys=[indicator_id])
    region = relationship("Region", foreign_keys=[region_id])

    __table_args__ = (
        CheckConstraint(
            "status IN ('ok', 'invalid', 'imported', 'skipped')",
            name="dir_status_chk",
        ),
        Index("idx_dir_job_status", "job_id", "status"),
        Index("idx_dir_indicator_region", "indicator_id", "region_id"),
    )


class IntakeMapping(Base):
    """UI-редактируемая конфигурация ETL для одной remote-таблицы reports_db."""
    __tablename__ = "intake_mappings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    remote_table = Column(Text, nullable=False)
    indicator_id = Column(
        UUID(as_uuid=True),
        ForeignKey("indicators.id", ondelete="RESTRICT"),
        nullable=False,
    )
    region_resolution_kind = Column(String(20), nullable=False, default="constant")
    kato_constant = Column(
        String(9),
        ForeignKey("kato_reference.kato_code", ondelete="RESTRICT"),
        nullable=True,
    )
    region_column_idx = Column(Integer, nullable=True)
    region_name_to_kato = Column(JSONB, nullable=True)

    period_kind = Column(String(20), nullable=False)
    year_column_idx = Column(Integer, nullable=True)
    period_columns_idx = Column(JSONB, nullable=False)
    skip_if_first_col_in = Column(JSONB, nullable=False, default=list)

    source_name = Column(Text, nullable=True)
    source_url = Column(Text, nullable=True)
    source_description = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)

    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    last_run_status = Column(String(20), nullable=True)
    last_run_summary = Column(JSONB, nullable=True)

    indicator = relationship("Indicator", foreign_keys=[indicator_id])
    kato = relationship("KatoReference", foreign_keys=[kato_constant])
    creator = relationship("User", foreign_keys=[created_by])

    __table_args__ = (
        CheckConstraint(
            "region_resolution_kind IN ('constant', 'by_name')",
            name="im_region_kind_chk",
        ),
        CheckConstraint(
            "period_kind IN ('yearly', 'quarterly', 'monthly', 'daily')",
            name="im_period_kind_chk",
        ),
        Index("idx_im_indicator", "indicator_id"),
        Index("idx_im_remote_table", "remote_table"),
        Index("idx_im_last_run_status", "last_run_status"),
    )


class IntakeQualityFlag(Base):
    """Замечание контроля качества по ключу значения (показатель, регион, период).

    Показывается эксперту по проверке рядом со значением «на проверке».
    """
    __tablename__ = "intake_quality_flags"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("dataset_intake_jobs.id", ondelete="CASCADE"), nullable=False)
    indicator_id = Column(UUID(as_uuid=True), nullable=False)
    region_id = Column(UUID(as_uuid=True), nullable=False)
    period_type = Column(String(20), nullable=False)
    period_start = Column(Date, nullable=False)
    code = Column(String(40), nullable=False)
    severity = Column(String(10), nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("severity IN ('blocker', 'warning', 'info')", name="iqf_severity_chk"),
        Index("idx_iqf_key", "indicator_id", "region_id", "period_type", "period_start"),
        Index("idx_iqf_job", "job_id"),
    )
