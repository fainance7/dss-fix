"""Read-only клиент к reports_db (первичные данные смежной команды).

Каждый запрос открывает свежий asyncpg-коннект и закрывает его. Без пула —
эндпоинты дёргаются редко, экономить контексты смысла нет, а пулу нужен общий
event loop, что усложняет setup.
"""
from typing import Any, Optional

import asyncpg

from app.config import settings


async def _connect() -> asyncpg.Connection:
    return await asyncpg.connect(
        host=settings.REPORTS_PGHOST,
        port=settings.REPORTS_PGPORT,
        user=settings.REPORTS_PGUSER,
        password=settings.REPORTS_PGPASSWORD,
        database=settings.REPORTS_PGDATABASE,
        ssl=False,
        timeout=10,
    )


async def list_tables(search: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
    """Список всех таблиц в public + размер (rows estimate, columns)."""
    conn = await _connect()
    try:
        sql = """
            SELECT
                t.table_name,
                COALESCE(c.reltuples::bigint, 0) AS row_estimate,
                (SELECT COUNT(*) FROM information_schema.columns col
                 WHERE col.table_schema='public' AND col.table_name=t.table_name) AS column_count
            FROM information_schema.tables t
            LEFT JOIN pg_class c ON c.relname = t.table_name AND c.relkind = 'r'
            WHERE t.table_schema = 'public'
              AND t.table_name <> 'spatial_ref_sys'
        """
        params: list[Any] = []
        if search:
            sql += " AND t.table_name ILIKE $1"
            params.append(f"%{search}%")
        sql += " ORDER BY t.table_name LIMIT $%d" % (len(params) + 1)
        params.append(limit)
        rows = await conn.fetch(sql, *params)
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def get_table_columns(table: str) -> list[dict[str, Any]]:
    conn = await _connect()
    try:
        rows = await conn.fetch(
            """
            SELECT ordinal_position - 1 AS index,
                   column_name,
                   data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1
            ORDER BY ordinal_position
            """,
            table,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def preview_rows(table: str, limit: int = 10) -> dict[str, Any]:
    """Возвращает {columns: [{index,name,type}], rows: [[...], ...]}.

    Возвращает значения как list-of-list (а не dict), потому что имена колонок
    в reports_db нестабильны и могут дублироваться (см. project_remote_excel_quirks).
    """
    conn = await _connect()
    try:
        cols = await conn.fetch(
            """
            SELECT ordinal_position - 1 AS index,
                   column_name,
                   data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1
            ORDER BY ordinal_position
            """,
            table,
        )
        if not cols:
            return {"columns": [], "rows": []}
        safe_table = table.replace('"', '""')
        sample = await conn.fetch(f'SELECT * FROM public."{safe_table}" LIMIT $1', limit)
        return {
            "columns": [dict(c) for c in cols],
            "rows": [[_serialize(v) for v in r] for r in sample],
        }
    finally:
        await conn.close()


def _serialize(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)
