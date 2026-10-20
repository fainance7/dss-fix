"""POST /data-intake/json — JSON-batch загрузка для смежной команды через intake portal.

Использует тот же ``intake_service``, что и Excel-путь:
job создаётся в ``status='pending'``/``'validated'``, commit выполняется отдельно
admin/exp_ver-пользователем (тот же endpoint, что и для Excel).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.rbac import require_roles
from app.database import get_db
from app.models.user import User
from app.schemas.data_intake import IntakeUploadResponse
from app.schemas.intake_batch import IntakeJsonRequest
from app.services import intake_service

router = APIRouter(prefix="/data-intake", tags=["data-intake"])


@router.post(
    "/json",
    response_model=IntakeUploadResponse,
    summary="Загрузка партии через JSON",
    description=(
        "Программный канал загрузки исторических данных. "
        "Каждая строка указывает indicator_id, region_id, period_* и value. "
        "Job создаётся в статусе pending/validated; финальный commit делает "
        "оператор DSS через /api/v1/data-intake/jobs/{id}/commit."
    ),
)
async def upload_json(
    payload: IntakeJsonRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader")),
) -> IntakeUploadResponse:
    response = await intake_service.create_job_from_rows(db, user, payload)

    await log_action(
        db,
        user_id=user.id,
        action="upload",
        entity_type="data_intake_job",
        entity_id=response.job.id,
        details={
            "source_kind": "json_api",
            "rows_total": response.job.rows_total,
            "rows_ok": response.job.rows_ok,
            "rows_invalid": response.job.rows_invalid,
        },
    )
    return response
