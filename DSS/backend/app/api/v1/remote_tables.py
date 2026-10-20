"""Read-only эндпоинты для UI-разметки таблиц reports_db (первичная БД).

Используются страницей /dashboard/data-intake/remote: список таблиц,
preview первых N строк, перечень колонок.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.rbac import require_roles
from app.models.user import User
from app.services import reports_db as reports_db_svc

router = APIRouter(prefix="/data-intake/remote", tags=["data-intake-remote"])


@router.get("/tables")
async def list_remote_tables(
    search: Optional[str] = Query(None, max_length=100),
    limit: int = Query(200, ge=1, le=2000),
    _: User = Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader")),
):
    try:
        return await reports_db_svc.list_tables(search=search, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"reports_db недоступна: {e}")


@router.get("/tables/{table}/preview")
async def preview_table(
    table: str,
    limit: int = Query(10, ge=1, le=100),
    _: User = Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader")),
):
    try:
        result = await reports_db_svc.preview_rows(table, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"reports_db недоступна: {e}")
    if not result["columns"]:
        raise HTTPException(status_code=404, detail=f"Таблица «{table}» не найдена")
    return result


@router.get("/tables/{table}/columns")
async def table_columns(
    table: str,
    _: User = Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader")),
):
    try:
        cols = await reports_db_svc.get_table_columns(table)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"reports_db недоступна: {e}")
    if not cols:
        raise HTTPException(status_code=404, detail=f"Таблица «{table}» не найдена")
    return cols
