"""Просмотр журнала аудита (AuditLog). Полный журнал — только ADM (ТЗ ФFE.7 / Приложение 2);
data_uploader видит только записи о загрузке данных (партии приёма, разметка, геоданные).

Записи создаются повсеместно через app.core.audit.log_action; этот роутер даёт
чтение с фильтрами и пагинацией для админ-панели мониторинга.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import require_roles
from app.database import get_db
from app.models.user import AuditLog, User

router = APIRouter(prefix="/audit-log", tags=["audit-log"])

# Сущности, связанные с загрузкой данных, — доступны роли data_uploader.
INTAKE_ENTITY_TYPES = (
    "data_intake_job",
    "intake_mapping",
    "meteo_station_values",
    "spatial_data",
    "gis_category_link",
    "gis_dataset_link",
)


def _scope_conditions(user: User) -> list:
    """Не-админ видит только записи о загрузке данных."""
    if any(ur.role.name == "admin" for ur in user.user_roles):
        return []
    return [AuditLog.entity_type.in_(INTAKE_ENTITY_TYPES)]


class AuditLogItem(BaseModel):
    id: UUID
    user_id: Optional[UUID]
    username: Optional[str]
    action: str
    entity_type: str
    entity_id: Optional[UUID]
    details: Optional[dict]
    ip_address: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogPage(BaseModel):
    items: list[AuditLogItem]
    total: int
    page: int
    page_size: int
    pages: int


def _filters(current_user, user_id, action, entity_type, date_from, date_to) -> list:
    conditions = _scope_conditions(current_user)
    if user_id is not None:
        conditions.append(AuditLog.user_id == user_id)
    if action:
        conditions.append(AuditLog.action == action)
    if entity_type:
        conditions.append(AuditLog.entity_type == entity_type)
    if date_from is not None:
        conditions.append(AuditLog.created_at >= date_from)
    if date_to is not None:
        conditions.append(AuditLog.created_at <= date_to)
    return conditions


EXPORT_LIMIT = 100_000


@router.get("/export")
async def export_audit_log(
    user_id: Optional[UUID] = None,
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "data_uploader")),
):
    """Выгрузка журнала аудита в CSV — все записи по фильтру (Прил. 2, ч. 4 ТЗ)."""
    import csv
    import io
    import json

    from fastapi.responses import Response

    q = select(AuditLog, User.username).outerjoin(User, AuditLog.user_id == User.id)
    for c in _filters(current_user, user_id, action, entity_type, date_from, date_to):
        q = q.where(c)
    rows = (await db.execute(q.order_by(AuditLog.created_at.desc()).limit(EXPORT_LIMIT))).all()

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Время (UTC)", "Пользователь", "Действие", "Объект", "ID объекта", "IP-адрес", "Подробности"])
    for log, username in rows:
        w.writerow([
            log.created_at.isoformat() if log.created_at else "",
            username or "",
            log.action,
            log.entity_type or "",
            str(log.entity_id) if log.entity_id else "",
            str(log.ip_address) if log.ip_address is not None else "",
            json.dumps(log.details, ensure_ascii=False) if log.details else "",
        ])
    # BOM — чтобы Excel открыл кириллицу без выбора кодировки.
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="audit_log.csv"'},
    )


@router.get("", response_model=AuditLogPage)
async def list_audit_log(
    user_id: Optional[UUID] = None,
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "data_uploader")),
):
    """Записи журнала аудита с фильтрами (пользователь, действие, сущность, период)."""
    conditions = _filters(current_user, user_id, action, entity_type, date_from, date_to)

    count_q = select(func.count()).select_from(AuditLog)
    for c in conditions:
        count_q = count_q.where(c)
    total = (await db.execute(count_q)).scalar_one()

    q = (
        select(AuditLog, User.username)
        .outerjoin(User, AuditLog.user_id == User.id)
    )
    for c in conditions:
        q = q.where(c)
    q = q.order_by(AuditLog.created_at.desc()).limit(page_size).offset((page - 1) * page_size)

    rows = (await db.execute(q)).all()
    items = [
        AuditLogItem(
            id=log.id,
            user_id=log.user_id,
            username=username,
            action=log.action,
            entity_type=log.entity_type,
            entity_id=log.entity_id,
            details=log.details,
            ip_address=str(log.ip_address) if log.ip_address is not None else None,
            created_at=log.created_at,
        )
        for log, username in rows
    ]
    pages = (total + page_size - 1) // page_size if total else 0
    return AuditLogPage(items=items, total=total, page=page, page_size=page_size, pages=pages)


@router.get("/actions", response_model=list[str])
async def list_actions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "data_uploader")),
):
    """Уникальные значения action — для выпадающего фильтра в UI."""
    q = select(AuditLog.action).distinct().order_by(AuditLog.action)
    for c in _scope_conditions(current_user):
        q = q.where(c)
    rows = (await db.execute(q)).scalars().all()
    return list(rows)
