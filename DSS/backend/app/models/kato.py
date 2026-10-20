import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class KatoReference(Base):
    __tablename__ = "kato_reference"

    kato_code    = Column(String(9),   primary_key=True)
    name_ru      = Column(String(250), nullable=False)
    name_kz      = Column(String(250), nullable=True)
    type_code    = Column(String(10),  nullable=True)
    type_name_ru = Column(String(100), nullable=True)
    parent_code  = Column(String(9),   ForeignKey("kato_reference.kato_code", ondelete="RESTRICT"), nullable=True)
    level        = Column(SmallInteger, nullable=False)
    is_active    = Column(Boolean,     nullable=False, default=True)
    region_id    = Column(UUID(as_uuid=True), ForeignKey("regions.id", ondelete="SET NULL"), nullable=True)
    created_at   = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    parent   = relationship("KatoReference", remote_side="KatoReference.kato_code", backref="children")
    region   = relationship("Region", foreign_keys=[region_id], backref="kato_entry")
