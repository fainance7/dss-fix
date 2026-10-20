import csv
import io
import logging
from collections import deque
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.cache import cache_get, cache_set
from app.core.rbac import require_roles
from app.database import AsyncSessionLocal, get_db
from app.deps import get_current_active_user
from app.models.kato import KatoReference
from app.models.user import User
from app.schemas.kato import KatoCreate, KatoImportStatus, KatoListResponse, KatoResponse, KatoUpdate

logger = logging.getLogger(__name__)

IMPORT_TTL = 3600
BATCH_SIZE = 500
PROGRESS_EVERY = 500

router = APIRouter(prefix="/kato", tags=["kato"])


@router.get("", response_model=KatoListResponse)
async def list_kato(
    level: Optional[int] = Query(None, ge=1, le=5, description="Уровень иерархии (1=область, 2=район)"),
    parent_code: Optional[str] = Query(None, description="Код родительского объекта КАТО"),
    search: Optional[str] = Query(None, min_length=2, description="Поиск по наименованию (рус.)"),
    code_search: Optional[str] = Query(None, min_length=1, description="Поиск по коду КАТО или коду родителя"),
    is_active: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    """Список объектов КАТО с фильтрацией."""
    q = select(KatoReference)
    if level is not None:
        q = q.where(KatoReference.level == level)
    if parent_code is not None:
        q = q.where(KatoReference.parent_code == parent_code)
    if is_active is not None:
        q = q.where(KatoReference.is_active == is_active)
    if search:
        q = q.where(KatoReference.name_ru.ilike(f"%{search}%"))
    if code_search:
        q = q.where(
            or_(
                KatoReference.kato_code.ilike(f"%{code_search}%"),
                KatoReference.parent_code.ilike(f"%{code_search}%"),
            )
        )
    q = q.order_by(KatoReference.kato_code)

    result = await db.execute(q)
    items = result.scalars().all()
    return KatoListResponse(
        items=[KatoResponse.model_validate(k) for k in items],
        total=len(items),
    )


@router.post("", response_model=KatoResponse, status_code=201)
async def create_kato(
    body: KatoCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    """Создать объект КАТО. Только admin."""
    existing = (
        await db.execute(select(KatoReference).where(KatoReference.kato_code == body.kato_code))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Код КАТО {body.kato_code} уже существует")

    obj = KatoReference(**body.model_dump())
    db.add(obj)
    await db.flush()
    await log_action(
        db,
        user_id=current_user.id,
        action="create",
        entity_type="kato_reference",
        details={"kato_code": obj.kato_code, **body.model_dump(exclude_none=True, mode="json")},
    )
    await db.refresh(obj)
    return KatoResponse.model_validate(obj)


@router.post("/import", status_code=202)
async def import_kato_csv(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    current_user: User = Depends(require_roles("admin")),
) -> dict:
    """Импорт КАТО из CSV-файла (полуколонный разделитель, UTF-8).
    Обязательные колонки: id, parent_id, code, name.
    Возвращает job_id для опроса статуса через GET /kato/import/{job_id}.
    """
    content = await file.read()

    try:
        sample = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(sample), delimiter=";")
        header = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - header
        if missing:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Файл не содержит обязательных колонок: {', '.join(sorted(missing))}. "
                    f"Найдены колонки: {', '.join(sorted(header)) or '(нет)'}."
                ),
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Ошибка чтения файла: {exc}") from exc

    job_id = str(uuid4())
    initial_state = {
        "status": "running",
        "total": 0,
        "processed": 0,
        "added": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
        "error_message": None,
    }
    await cache_set(f"kato_import:{job_id}", initial_state, IMPORT_TTL)

    background_tasks.add_task(
        _process_kato_import,
        content=content,
        job_id=job_id,
        user_id=current_user.id,
        filename=file.filename or "unknown.csv",
    )
    return {"job_id": job_id}


@router.get("/import/{job_id}", response_model=KatoImportStatus)
async def get_kato_import_status(
    job_id: str,
    _: User = Depends(require_roles("admin")),
) -> KatoImportStatus:
    """Статус фонового импорта КАТО по job_id."""
    data = await cache_get(f"kato_import:{job_id}")
    if data is None:
        raise HTTPException(status_code=404, detail="Задача импорта не найдена или истёк срок хранения")
    return KatoImportStatus(**data)


@router.get("/{kato_code}", response_model=KatoResponse)
async def get_kato(
    kato_code: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    """Получить объект КАТО по коду."""
    result = await db.execute(
        select(KatoReference).where(KatoReference.kato_code == kato_code)
    )
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Объект КАТО с кодом {kato_code} не найден",
        )
    return KatoResponse.model_validate(obj)


@router.patch("/{kato_code}", response_model=KatoResponse)
async def update_kato(
    kato_code: str,
    body: KatoUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    """Обновить объект КАТО. Только admin."""
    result = await db.execute(select(KatoReference).where(KatoReference.kato_code == kato_code))
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail=f"Объект КАТО с кодом {kato_code} не найден")

    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(obj, k, v)

    await log_action(
        db,
        user_id=current_user.id,
        action="update",
        entity_type="kato_reference",
        details={"kato_code": kato_code, **body.model_dump(exclude_none=True, mode="json")},
    )
    await db.flush()
    await db.refresh(obj)
    return KatoResponse.model_validate(obj)


@router.delete("/{kato_code}", status_code=204)
async def delete_kato(
    kato_code: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    """Удалить объект КАТО. Только admin. Нельзя удалить, если есть дочерние записи."""
    result = await db.execute(select(KatoReference).where(KatoReference.kato_code == kato_code))
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail=f"Объект КАТО с кодом {kato_code} не найден")

    children = (
        await db.execute(select(KatoReference).where(KatoReference.parent_code == kato_code).limit(1))
    ).scalar_one_or_none()
    if children is not None:
        raise HTTPException(
            status_code=409,
            detail="Невозможно удалить: существуют дочерние объекты КАТО",
        )

    await db.delete(obj)
    await log_action(
        db,
        user_id=current_user.id,
        action="delete",
        entity_type="kato_reference",
        details={"kato_code": kato_code},
    )


# ─── KATO Import ─────────────────────────────────────────────────────────────

REQUIRED_COLUMNS = {"id", "parent_id", "code", "name"}


async def _process_kato_import(
    content: bytes,
    job_id: str,
    user_id: UUID,
    filename: str,
) -> None:
    """Background task: parse CSV, diff against kato_reference, upsert changes."""
    redis_key = f"kato_import:{job_id}"

    async def _update_redis(**kwargs: object) -> None:
        current = await cache_get(redis_key) or {}
        current.update(kwargs)
        await cache_set(redis_key, current, IMPORT_TTL)

    try:
        text = content.decode("utf-8-sig")  # handle BOM if present
        reader = csv.DictReader(io.StringIO(text), delimiter=";")
        rows = list(reader)

        total = len(rows)
        await _update_redis(total=total)

        # ── Build id→code mapping and compute levels via BFS ──────────────────
        id_to_code: dict[str, str] = {}
        children_of: dict[str, list[str]] = {}  # parent_id → [child_ids]
        for row in rows:
            rid = row["id"].strip()
            code = row["code"].strip()
            pid = row["parent_id"].strip()
            id_to_code[rid] = code
            children_of.setdefault(pid, []).append(rid)

        id_to_level: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque()
        for root_id in children_of.get("0", []):
            queue.append((root_id, 1))

        while queue:
            rid, lvl = queue.popleft()
            id_to_level[rid] = min(lvl, 5)  # cap at 5 (CHECK constraint)
            for child_id in children_of.get(rid, []):
                queue.append((child_id, lvl + 1))

        # ── Load existing kato_reference in one query ──────────────────────────
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(KatoReference.kato_code, KatoReference.name_ru))
            existing: dict[str, str] = {row.kato_code: row.name_ru for row in result}

            added = updated = skipped = processed = 0
            errors: list[str] = []
            to_insert: list[KatoReference] = []
            to_update: list[tuple[str, str]] = []  # (kato_code, new_name_ru)

            for row in rows:
                processed += 1
                try:
                    rid = row["id"].strip()
                    pid = row["parent_id"].strip()
                    code = row["code"].strip()
                    name = row["name"].strip()

                    if not code or not name:
                        errors.append(f"Строка {rid}: пустой код или наименование — пропущено")
                        skipped += 1
                        continue

                    parent_code = id_to_code.get(pid) if pid != "0" else None
                    level = id_to_level.get(rid, 1)

                    if code not in existing:
                        to_insert.append(
                            KatoReference(
                                kato_code=code,
                                name_ru=name,
                                parent_code=parent_code,
                                level=level,
                                is_active=True,
                            )
                        )
                        added += 1
                    elif existing[code] != name:
                        to_update.append((code, name))
                        updated += 1
                    else:
                        skipped += 1

                except Exception as e:
                    errors.append(f"Строка {row.get('id', '?')}: {e}")

                if processed % PROGRESS_EVERY == 0:
                    await _update_redis(
                        processed=processed,
                        added=added,
                        updated=updated,
                        skipped=skipped,
                    )

            # ── Flush inserts in level-sorted batches ──────────────────────────
            to_insert.sort(key=lambda k: k.level)
            for i in range(0, len(to_insert), BATCH_SIZE):
                batch = to_insert[i : i + BATCH_SIZE]
                db.add_all(batch)
                await db.flush()

            # ── Execute updates ────────────────────────────────────────────────
            for code, name in to_update:
                await db.execute(
                    update(KatoReference)
                    .where(KatoReference.kato_code == code)
                    .values(name_ru=name)
                )

            await log_action(
                db,
                user_id=user_id,
                action="kato_import",
                entity_type="kato_reference",
                details={
                    "filename": filename,
                    "total": total,
                    "added": added,
                    "updated": updated,
                    "skipped": skipped,
                    "errors": errors[:50],
                },
            )
            await db.commit()

        await _update_redis(
            status="done",
            processed=total,
            added=added,
            updated=updated,
            skipped=skipped,
            errors=errors[:50],
        )
        logger.info("KATO import done: added=%d updated=%d skipped=%d", added, updated, skipped)

    except Exception as exc:
        logger.exception("KATO import failed: %s", exc)
        await _update_redis(status="error", error_message=str(exc))
