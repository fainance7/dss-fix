"""
POST /api/v1/dashboard-summaries

Краткий вывод ИИ по вкладке дашборда направления. Фронт передаёт ключ вкладки и
показатели, которые на ней показаны; сервис считает факты, сравнивает их
отпечаток с сохранённым и отдаёт готовый текст. Генерация (в фоне, локальная
LLM) запускается только когда отпечаток изменился — появились новые данные.
"""
import asyncio
import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal, get_db
from app.services import dashboard_summary as ds

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard-summaries", tags=["dashboard-summaries"])

# Ссылки на фоновые задачи генерации — иначе сборщик мусора может снять их до конца.
_TASKS: set[asyncio.Task] = set()
# Не больше двух генераций одновременно: резервная LLM работает на CPU.
_LLM_SLOTS = asyncio.Semaphore(2)


class SummaryRequest(BaseModel):
    page_key: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_:/-]+$")
    title: str = Field(min_length=1, max_length=200)
    direction_code: str = Field(min_length=1, max_length=50)
    indicator_ids: list[UUID] = Field(max_length=40)


class SummaryResponse(BaseModel):
    page_key: str
    # done — текст актуален; running — генерируется (text — прежний вывод, если был);
    # empty — по показателям вкладки нет данных.
    status: str
    text: str | None = None
    source: str | None = None
    generated_at: datetime | None = None


async def _generate(
    page_key: str, fp: str, direction_name: str, title: str, facts: list[ds.IndicatorFact],
) -> None:
    async with _LLM_SLOTS:
        try:
            summary, source = await ds.generate_summary(direction_name, title, facts)
        except Exception:  # noqa: BLE001 — фоновая задача: вывод не должен остаться в running
            logger.exception("Вывод дашборда %s: сбой генерации", page_key)
            summary, source = ds.template_summary(facts), "template"
    async with AsyncSessionLocal() as db:
        # fingerprint в условии: пока шла генерация, данные могли обновиться ещё раз.
        await db.execute(text("""
            UPDATE dashboard_summaries
               SET summary = :s, source = :src, model = :m, status = 'done',
                   error = NULL, generated_at = now(), updated_at = now()
             WHERE page_key = :k AND fingerprint = :f
        """), {"s": summary, "src": source, "m": settings.LLM_MODEL, "k": page_key, "f": fp})
        await db.commit()


async def release_orphaned_summaries() -> None:
    """Генерации, прерванные рестартом сервиса, сразу отдаются на повторный захват
    (иначе вкладка ждала бы 15 минут)."""
    async with AsyncSessionLocal() as db:
        await db.execute(text("""
            UPDATE dashboard_summaries SET updated_at = now() - interval '1 hour'
             WHERE status = 'running'
        """))
        await db.commit()


@router.post("", response_model=SummaryResponse)
async def resolve_summary(body: SummaryRequest, db: AsyncSession = Depends(get_db)):
    direction = (await db.execute(
        text("SELECT name FROM directions WHERE code = :c"), {"c": body.direction_code},
    )).first()
    facts = await ds.collect_facts(db, body.direction_code, body.indicator_ids) if direction else []
    if not facts:
        return SummaryResponse(page_key=body.page_key, status="empty")

    fp = ds.fingerprint(body.title, ds.render_facts(facts))
    # Захват генерации: строка обновляется, только если данные изменились, прошлая
    # генерация зависла или сохранён запасной шаблонный текст (LLM была недоступна).
    claimed = (await db.execute(text("""
        INSERT INTO dashboard_summaries (page_key, direction_code, title, fingerprint, status)
        VALUES (:k, :d, :t, :f, 'running')
        ON CONFLICT (page_key) DO UPDATE
           SET direction_code = EXCLUDED.direction_code, title = EXCLUDED.title,
               fingerprint = EXCLUDED.fingerprint, status = 'running', updated_at = now()
         WHERE dashboard_summaries.fingerprint <> EXCLUDED.fingerprint
            OR (dashboard_summaries.status = 'running'
                AND dashboard_summaries.updated_at < now() - interval '15 minutes')
            OR (dashboard_summaries.status = 'done' AND dashboard_summaries.source = 'template'
                AND dashboard_summaries.updated_at < now() - interval '30 minutes')
        RETURNING page_key
    """), {"k": body.page_key, "d": body.direction_code, "t": body.title, "f": fp})).first()
    await db.commit()

    if claimed:
        task = asyncio.create_task(_generate(body.page_key, fp, direction[0], body.title, facts))
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)

    row = (await db.execute(text("""
        SELECT status, summary, source, generated_at
        FROM dashboard_summaries WHERE page_key = :k
    """), {"k": body.page_key})).mappings().one()
    return SummaryResponse(
        page_key=body.page_key, status=row["status"], text=row["summary"],
        source=row["source"], generated_at=row["generated_at"],
    )
