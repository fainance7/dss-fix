import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer,
    SmallInteger, String, Text, UniqueConstraint, func
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base


class Region(Base):
    __tablename__ = "regions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(20), nullable=False, unique=True)
    name = Column(String(200), nullable=False)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("regions.id", ondelete="RESTRICT"), nullable=True)
    level = Column(SmallInteger, nullable=False)
    kato_code = Column(String(9), ForeignKey("kato_reference.kato_code", ondelete="SET NULL", use_alter=True, name="fk_regions_kato_code"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    parent = relationship("Region", remote_side=[id], backref="children")
    user_roles = relationship("UserRole", back_populates="region")
    kato = relationship("KatoReference", foreign_keys=[kato_code])


class Role(Base):
    __tablename__ = "roles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(50), nullable=False, unique=True)
    description = Column(Text, nullable=True)

    user_roles = relationship("UserRole", back_populates="role")


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(100), nullable=False, unique=True)
    email = Column(String(255), nullable=False, unique=True)
    iin = Column(String(12), nullable=True, unique=True)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    # Версия токенов (M-7): увеличение отзывает все выданные пользователю токены.
    token_version = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    user_roles = relationship("UserRole", back_populates="user", cascade="all, delete-orphan")
    user_directions = relationship("UserDirection", back_populates="user", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="user")


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT"), primary_key=True)
    # region_id is NOT part of PK — PostgreSQL forbids NULL in PK columns.
    # A NULL region_id means the role applies globally (not scoped to a region).
    region_id = Column(UUID(as_uuid=True), ForeignKey("regions.id", ondelete="SET NULL"), nullable=True)
    assigned_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = relationship("User", back_populates="user_roles")
    role = relationship("Role", back_populates="user_roles")
    region = relationship("Region", back_populates="user_roles")


class UserDirection(Base):
    __tablename__ = "user_directions"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    direction_id = Column(UUID(as_uuid=True), ForeignKey("directions.id", ondelete="CASCADE"), primary_key=True)
    assigned_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = relationship("User", back_populates="user_directions")
    direction = relationship("Direction")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(100), nullable=False)
    entity_type = Column(String(100), nullable=False)
    entity_id = Column(UUID(as_uuid=True), nullable=True)
    details = Column(JSONB, nullable=True)
    ip_address = Column(INET, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = relationship("User", back_populates="audit_logs")

    __table_args__ = (
        Index("idx_audit_log_user_id", "user_id"),
        Index("idx_audit_log_created_at", "created_at"),
        Index("idx_audit_log_entity", "entity_type", "entity_id"),
    )
