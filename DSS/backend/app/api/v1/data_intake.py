"""Загрузка исторических данных через Excel-шаблон.

Workflow:
  1. POST /excel — multipart upload → парсинг + валидация → DataIntakeJob (status='validated' или 'pending').
  2. GET /jobs, GET /jobs/{id} — просмотр.
  3. POST /jobs/{id}/commit — UPSERT ok-строк в indicator_values.
  4. DELETE /jobs/{id} — отмена партии до коммита.
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import log_action
from app.core.rbac import get_user_allowed_directions, require_roles
from app.database import get_db
from app.models.data import Direction, IndicatorValue
from app.models.data_intake import DataIntakeJob, DataIntakeRow
from app.models.user import User
from app.schemas.data_intake import (
    IntakeCommitRequest,
    IntakeCommitResult,
    IntakeJobDetailOut,
    IntakeJobListResponse,
    IntakeJobOut,
    IntakePreviewResult,
    IntakeRevalidateResult,
    IntakeRowOut,
    IntakeUploadResponse,
)
from app.services import intake_quality, intake_service

router = APIRouter(prefix="/data-intake", tags=["data-intake"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB

# Путь к manuals/data_intake_template.xlsx. По умолчанию — относительно корня репозитория
# (нужно для locale-dev запуска). В контейнере монтируем /manuals или задаём INTAKE_TEMPLATE_PATH.
import os as _os
TEMPLATE_PATH = Path(
    _os.environ.get("INTAKE_TEMPLATE_PATH")
    or (Path(__file__).resolve().parents[4] / "manuals" / "data_intake_template.xlsx")
)


@router.get("/template")
async def download_template(
    _: User = Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader")),
):
    if not TEMPLATE_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="Шаблон не сгенерирован. Запустите scripts/generate_intake_template.py --from-db",
        )
    return FileResponse(
        TEMPLATE_PATH,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="data_intake_template.xlsx",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


PROTOCOL_PATH = Path(
    _os.environ.get("INTAKE_PROTOCOL_PATH")
    or (Path(__file__).resolve().parents[4] / "manuals" / "data_intake_protocol.md")
)


@router.get("/protocol")
async def get_protocol(
    format: str = Query("html", pattern="^(html|md)$"),
    _: User = Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader")),
):
    if not PROTOCOL_PATH.exists():
        raise HTTPException(status_code=404, detail="Протокол не найден на сервере")
    text = PROTOCOL_PATH.read_text(encoding="utf-8")
    if format == "md":
        return FileResponse(PROTOCOL_PATH, media_type="text/markdown",
                            filename="data_intake_protocol.md")
    return {"html": _safe_markdown_html(text), "markdown": text}


def _safe_markdown_html(text: str) -> str:
    """Markdown → HTML без исполняемого содержимого (M-9): страница вставляет HTML
    как есть, поэтому сырой HTML из исходника экранируется, а ссылки допускаются
    только http(s), mailto и внутренние."""
    import re

    import markdown as _md

    md = _md.Markdown(extensions=["tables", "fenced_code", "toc", "sane_lists"], output_format="html5")
    md.preprocessors.deregister("html_block")
    md.inlinePatterns.deregister("html")
    html = md.convert(text)
    return re.sub(
        r'(href|src)="(?!https?:|mailto:|#|/)[^"]*"',
        lambda m: f'{m.group(1)}="#"',
        html,
        flags=re.IGNORECASE,
    )


def _row_to_out(r: DataIntakeRow) -> IntakeRowOut:
    return IntakeRowOut(
        id=r.id,
        row_no=r.row_no,
        status=r.status,
        indicator_id=r.indicator_id,
        indicator_name=r.indicator.name if r.indicator else None,
        region_id=r.region_id,
        region_name=(r.region.name if r.region else
                     (f"метеостанция {(r.raw_payload or {}).get('station') or ''}".strip()
                      if r.station_id else None)),
        period_type=r.period_type,
        period_start=r.period_start,
        period_end=r.period_end,
        value=r.value,
        error_message=r.error_message,
        raw_payload=r.raw_payload or {},
    )


async def _enforce_upload_direction_scope(db: AsyncSession, user: User, parsed) -> None:
    """G10: exp_dir может грузить данные только по своим направлениям.

    Для ограниченного exp_dir (есть user_directions) проверяем, что все
    direction_code в файле входят в разрешённые направления. admin/exp_ver/
    data_uploader и exp_dir без ограничений — не лимитируются.
    """
    allowed = get_user_allowed_directions(user)
    if allowed is None:
        return
    codes = {
        str(r.raw.get("direction_code")).strip()
        for r in parsed.rows
        if r.raw.get("direction_code")
    }
    codes = {c for c in codes if c}
    if not codes:
        return
    rows = (await db.execute(
        select(Direction.id, Direction.code).where(Direction.code.in_(codes))
    )).all()
    code_to_id = {code: did for did, code in rows}
    for code in sorted(codes):
        direction_id = code_to_id.get(code)
        if direction_id is None or direction_id not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Нет прав на загрузку данных направления '{code}'",
            )


@router.post("/excel", response_model=IntakeUploadResponse)
async def upload_excel(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader")),
):
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Ожидается файл .xlsx")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Файл больше 20 МБ")

    try:
        parsed = intake_service.parse_excel_template(content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # G10: ограничение по направлению для exp_dir — до валидации/сохранения.
    await _enforce_upload_direction_scope(db, current_user, parsed)

    parsed = await intake_service.validate_rows(db, parsed)

    job = await intake_service.persist_job_with_rows(
        db,
        parsed=parsed,
        source_kind="excel_template",
        source_ref=file.filename,
        user=current_user,
    )

    rows = (
        await db.execute(
            select(DataIntakeRow)
            .options(
                selectinload(DataIntakeRow.indicator),
                selectinload(DataIntakeRow.region),
            )
            .where(DataIntakeRow.job_id == job.id)
            .order_by(DataIntakeRow.row_no)
        )
    ).scalars().all()

    await log_action(
        db,
        user_id=current_user.id,
        action="upload",
        entity_type="data_intake_job",
        entity_id=job.id,
        details={
            "filename": file.filename,
            "rows_total": job.rows_total,
            "rows_ok": job.rows_ok,
            "rows_invalid": job.rows_invalid,
        },
    )

    return IntakeUploadResponse(
        job=IntakeJobOut.model_validate(job),
        rows=[_row_to_out(r) for r in rows],
        errors_total=job.rows_invalid,
    )


def _own_jobs_only(user: User) -> bool:
    """M-4: эксперт, ограниченный направлениями, видит только свои партии — в чужих
    лежат исходные строки других направлений."""
    return get_user_allowed_directions(user) is not None


def _ensure_job_visible(job: DataIntakeJob | None, user: User) -> DataIntakeJob:
    if job is None or (_own_jobs_only(user) and job.uploaded_by != user.id):
        raise HTTPException(status_code=404, detail="Партия не найдена")
    return job


@router.get("/jobs", response_model=IntakeJobListResponse)
async def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
    source_kind: Optional[str] = Query(None),
    uploaded_by: Optional[UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader")),
):
    q = select(DataIntakeJob)
    if _own_jobs_only(current_user):
        q = q.where(DataIntakeJob.uploaded_by == current_user.id)
    if status:
        q = q.where(DataIntakeJob.status == status)
    if source_kind:
        q = q.where(DataIntakeJob.source_kind == source_kind)
    if uploaded_by:
        q = q.where(DataIntakeJob.uploaded_by == uploaded_by)

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = (
        await db.execute(
            q.order_by(DataIntakeJob.uploaded_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    return IntakeJobListResponse(
        items=[IntakeJobOut.model_validate(j) for j in items],
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size if total else 0,
    )


@router.get("/jobs/{job_id}", response_model=IntakeJobDetailOut)
async def get_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader")),
):
    job = _ensure_job_visible((
        await db.execute(
            select(DataIntakeJob).where(DataIntakeJob.id == job_id)
        )
    ).scalar_one_or_none(), current_user)

    rows = (
        await db.execute(
            select(DataIntakeRow)
            .options(
                selectinload(DataIntakeRow.indicator),
                selectinload(DataIntakeRow.region),
            )
            .where(DataIntakeRow.job_id == job_id)
            .order_by(DataIntakeRow.row_no)
        )
    ).scalars().all()

    stats_rows = (
        await db.execute(
            select(IndicatorValue.validation_status, func.count())
            .select_from(DataIntakeRow)
            .join(
                IndicatorValue,
                (IndicatorValue.indicator_id == DataIntakeRow.indicator_id)
                & (IndicatorValue.region_id == DataIntakeRow.region_id)
                & (IndicatorValue.period_type == DataIntakeRow.period_type)
                & (IndicatorValue.period_start == DataIntakeRow.period_start),
            )
            .where(
                DataIntakeRow.job_id == job_id,
                DataIntakeRow.status == "imported",
            )
            .group_by(IndicatorValue.validation_status)
        )
    ).all()
    value_stats = {"pending": 0, "approved": 0, "rejected": 0}
    matched = 0
    for status, cnt in stats_rows:
        value_stats[status] = cnt
        matched += cnt
    # Декады метеостанций партии хранятся в meteo_station_values (по job_id).
    from app.models.data import MeteoStationValue
    for status, cnt in (await db.execute(
        select(MeteoStationValue.validation_status, func.count())
        .where(MeteoStationValue.job_id == job_id, MeteoStationValue.period_type == "dekad")
        .group_by(MeteoStationValue.validation_status)
    )).all():
        value_stats[status] = value_stats.get(status, 0) + cnt
        matched += cnt
    # imported-строки, чьё значение уже перезаписано более новой партией
    # (по unique-ключу indicator/region/period осталось другое value).
    value_stats["missing"] = max(0, job.rows_imported - matched)

    base = IntakeJobOut.model_validate(job).model_dump()
    base["rows"] = [_row_to_out(r) for r in rows]
    base["value_stats"] = value_stats
    return IntakeJobDetailOut(**base)


@router.get("/jobs/{job_id}/preview", response_model=IntakePreviewResult)
async def preview_intake_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader")),
):
    """Dry-run отчёт: сколько строк партии новые / изменятся / без изменений и что на что.

    Ничего не пишет в indicator_values — только сравнивает с текущим состоянием.
    """
    job = (
        await db.execute(select(DataIntakeJob).where(DataIntakeJob.id == job_id))
    ).scalar_one_or_none()
    if job is None or not _ensure_job_visible(job, current_user):
        raise HTTPException(status_code=404, detail="Партия не найдена")
    return await intake_service.preview_job(db, job=job)


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


@router.get("/jobs/{job_id}/quality")
async def get_job_quality(
    job_id: UUID,
    refresh: bool = Query(False, description="Пересчитать отчёт (только для неимпортированной партии)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader")),
):
    """Отчёт контроля качества партии: повторная загрузка, коллизии ключа, смена единиц,
    изменение утверждённых значений, обрыв ряда, сумма районов и т. д.

    Для импортированной партии возвращается снимок, сделанный перед импортом.
    """
    job = (
        await db.execute(select(DataIntakeJob).where(DataIntakeJob.id == job_id))
    ).scalar_one_or_none()
    if job is None or not _ensure_job_visible(job, current_user):
        raise HTTPException(status_code=404, detail="Партия не найдена")
    if job.status != "imported" and (refresh or not job.quality_report):
        report = await intake_quality.refresh(db, job)
        await db.commit()
        return report
    return job.quality_report or {"summary": {"blocker": 0, "warning": 0, "info": 0}, "findings": [],
                                  "not_computed": True}


@router.post("/jobs/{job_id}/commit", response_model=IntakeCommitResult)
async def commit_intake_job(
    job_id: UUID,
    body: Optional[IntakeCommitRequest] = Body(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver")),
):
    job = (
        await db.execute(select(DataIntakeJob).where(DataIntakeJob.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Партия не найдена")
    if job.status == "imported":
        raise HTTPException(status_code=409, detail="Партия уже импортирована")
    if job.rows_ok == 0:
        raise HTTPException(
            status_code=400, detail="В партии нет валидных строк для коммита"
        )

    # Контроль качества пересчитывается на момент импорта: блокеры требуют
    # подтверждения эксперта с комментарием (импорт не запрещается).
    report = await intake_quality.refresh(db, job)
    n_blockers = intake_quality.blockers(report)
    comment = ((body.comment if body else None) or "").strip()
    if n_blockers:
        if not (body and body.acknowledge) or len(comment) < 10:
            await db.commit()  # отчёт сохранён, чтобы эксперт его увидел
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        f"В партии {n_blockers} {_plural(n_blockers, 'замечание', 'замечания', 'замечаний')} "
                        "уровня «блокер». Импорт возможен только "
                        "с подтверждением и комментарием эксперта (не короче 10 символов)."
                    ),
                    "blockers": n_blockers,
                },
            )
        report = {
            **report,
            "acknowledged": {
                "user_id": str(current_user.id),
                "username": current_user.username,
                "at": datetime.now(timezone.utc).isoformat(),
                "comment": comment,
            },
        }
        job.quality_report = report

    written, skipped, errors = await intake_service.commit_job(
        db, job=job, user=current_user
    )

    await log_action(
        db,
        user_id=current_user.id,
        action="commit",
        entity_type="data_intake_job",
        entity_id=job.id,
        details={
            "written": written, "skipped": skipped, "errors": errors[:5],
            "quality": report.get("summary"),
            "quality_comment": comment or None,
        },
    )
    return IntakeCommitResult(
        job_id=job.id,
        inserted_or_updated=written,
        skipped=skipped,
        errors=errors,
    )


@router.post("/jobs/{job_id}/revalidate", response_model=IntakeRevalidateResult)
async def revalidate_intake_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "data_uploader")),
):
    """Перепроверяет invalid-строки партии после пополнения справочников.

    Полезно, когда смежная команда загрузила данные с показателями, которых
    ещё не было в системе: после добавления показателей ревалидация резолвит
    строки без повторной загрузки файла.
    """
    job = (
        await db.execute(select(DataIntakeJob).where(DataIntakeJob.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Партия не найдена")
    if job.status == "imported":
        raise HTTPException(
            status_code=409,
            detail="Партия уже импортирована — перепроверка недоступна",
        )

    try:
        rechecked, became_ok, still_invalid = await intake_service.revalidate_job(
            db, job=job
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    await log_action(
        db,
        user_id=current_user.id,
        action="revalidate",
        entity_type="data_intake_job",
        entity_id=job.id,
        details={
            "rechecked": rechecked,
            "became_ok": became_ok,
            "still_invalid": still_invalid,
        },
    )

    await db.refresh(job)
    return IntakeRevalidateResult(
        job=IntakeJobOut.model_validate(job),
        rechecked=rechecked,
        became_ok=became_ok,
        still_invalid=still_invalid,
    )


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver")),
):
    job = (
        await db.execute(select(DataIntakeJob).where(DataIntakeJob.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Партия не найдена")
    if job.status == "imported":
        raise HTTPException(
            status_code=409,
            detail="Импортированную партию удалить нельзя — данные уже в indicator_values",
        )
    await db.delete(job)
    await log_action(
        db,
        user_id=current_user.id,
        action="delete",
        entity_type="data_intake_job",
        entity_id=job_id,
    )
