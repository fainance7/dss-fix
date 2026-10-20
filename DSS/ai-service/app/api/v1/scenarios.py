import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal, get_db
from app.models.ai_models import DevelopmentScenario
from app.schemas.scenario import (
    GenerateAllNarrativesRequest,
    GenerateNarrativeRequest,
    GenerateScenariosRequest,
    NarrativeStatusResponse,
    ScenarioCompare,
    ScenarioCreate,
    ScenarioResponse,
)
from sqlalchemy.orm.attributes import flag_modified

from app.services import narrative
from app.services.geo_context import build_geo_context, render_context_as_text
from app.services.llm_client import LLMUnavailableError
from app.services.scenarios import calculate_scenario, generate_all_scenarios
from app.services.tasks import generate_tasks_from_scenario

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scenarios", tags=["scenarios"])

# Удерживаем ссылки на фоновые задачи генерации нарратива: asyncio.create_task
# не держит strong-ссылку, и задача может быть собрана GC до завершения.
_NARRATIVE_TASKS: set = set()


def _to_response(s: DevelopmentScenario) -> ScenarioResponse:
    return ScenarioResponse(
        id=s.id, name=s.name, scenario_type=s.scenario_type, target_year=s.target_year,
        region_id=s.region_id, parameters=s.parameters, status=s.status,
        narrative=getattr(s, "narrative", None),
        narrative_generated_at=getattr(s, "narrative_generated_at", None),
        narrative_status=getattr(s, "narrative_status", None),
        narrative_error=getattr(s, "narrative_error", None),
        narratives=getattr(s, "narratives", None) or {},
        approved_by=s.approved_by, approved_at=s.approved_at,
        created_by=s.created_by, created_at=s.created_at, updated_at=s.updated_at,
    )


async def _get_or_404(db: AsyncSession, scenario_id: UUID) -> DevelopmentScenario:
    result = await db.execute(select(DevelopmentScenario).where(DevelopmentScenario.id == scenario_id))
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Сценарий {scenario_id} не найден")
    return obj


@router.post("", response_model=ScenarioResponse, status_code=status.HTTP_201_CREATED)
async def create_scenario(body: ScenarioCreate, db: AsyncSession = Depends(get_db)):
    """Создаёт новый сценарий с заданными параметрами."""
    scenario = DevelopmentScenario(
        name=body.name, scenario_type=body.scenario_type, target_year=body.target_year,
        region_id=body.region_id, parameters=body.parameters.model_dump(),
        status="draft", created_by=body.created_by,
    )
    db.add(scenario)
    await db.flush()
    await db.refresh(scenario)
    return _to_response(scenario)


@router.post("/generate", response_model=list[ScenarioResponse], status_code=status.HTTP_201_CREATED)
async def generate_scenarios(body: GenerateScenariosRequest, db: AsyncSession = Depends(get_db)):
    """Генерирует все 3 стандартных сценария (optimal/stable/risk) для региона."""
    try:
        results = await generate_all_scenarios(
            db=db, region_id=body.region_id, target_year=body.target_year, created_by=body.created_by,
        )
    except Exception as exc:
        logger.error("Ошибка генерации сценариев: %s", exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Ошибка генерации сценариев")

    # Генерируем задачи для каждого сценария
    scenario_ids = [UUID(r["scenario_id"]) for r in results]
    for sid in scenario_ids:
        try:
            await generate_tasks_from_scenario(db, sid, require_approved=False)
        except Exception as exc:
            logger.warning("Не удалось создать задачи для сценария %s: %s", sid, exc)

    await db.commit()

    # Возвращаем полные объекты из БД
    db_result = await db.execute(
        select(DevelopmentScenario).where(DevelopmentScenario.id.in_(scenario_ids))
    )
    scenarios = db_result.scalars().all()
    return [_to_response(s) for s in scenarios]


@router.get("/compare", response_model=ScenarioCompare)
async def compare_scenarios(
    ids: str = Query(..., description="UUID через запятую"),
    db: AsyncSession = Depends(get_db),
):
    """Сравнивает несколько сценариев. Пример: ?ids=uuid1,uuid2,uuid3"""
    raw_ids = [s.strip() for s in ids.split(",") if s.strip()]
    if not raw_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Укажите хотя бы один ID")
    parsed_ids = []
    for raw in raw_ids:
        try:
            parsed_ids.append(UUID(raw))
        except ValueError:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Некорректный UUID: {raw}")

    result = await db.execute(select(DevelopmentScenario).where(DevelopmentScenario.id.in_(parsed_ids)))
    scenarios = result.scalars().all()
    if not scenarios:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сценарии не найдены")
    return ScenarioCompare(scenarios=[_to_response(s) for s in scenarios], total=len(scenarios))


@router.get("", response_model=list[ScenarioResponse])
async def list_scenarios(
    region_id: Optional[UUID] = None,
    scenario_type: Optional[str] = Query(None, pattern="^(optimal|stable|risk|custom)$"),
    scenario_status: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    q = select(DevelopmentScenario)
    if region_id is not None:
        q = q.where(DevelopmentScenario.region_id == region_id)
    if scenario_type is not None:
        q = q.where(DevelopmentScenario.scenario_type == scenario_type)
    if scenario_status is not None:
        q = q.where(DevelopmentScenario.status == scenario_status)
    result = await db.execute(
        q.order_by(DevelopmentScenario.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return [_to_response(s) for s in result.scalars().all()]


@router.get("/{scenario_id}", response_model=ScenarioResponse)
async def get_scenario(scenario_id: UUID, db: AsyncSession = Depends(get_db)):
    return _to_response(await _get_or_404(db, scenario_id))


@router.post("/{scenario_id}/calculate", response_model=ScenarioResponse)
async def trigger_calculate(scenario_id: UUID, db: AsyncSession = Depends(get_db)):
    """Запускает расчёт KPI для сценария."""
    await _get_or_404(db, scenario_id)
    try:
        await calculate_scenario(db, scenario_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        logger.error("Ошибка расчёта сценария %s: %s", scenario_id, exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Ошибка расчёта сценария")
    return _to_response(await _get_or_404(db, scenario_id))


class PatchParametersBody(BaseModel):
    investment_multiplier: Optional[float] = None
    population_growth_rate: Optional[float] = None
    infrastructure_index: Optional[float] = None
    employment_target: Optional[float] = None
    gdp_growth_target: Optional[float] = None


LANDSCAPE_VARIANTS = ("lp-v1-conservative", "lp-v2-balanced", "lp-v3-development")


class LandscapeLinkBody(BaseModel):
    """Снимок варианта ландшафтного планирования для сценария.

    Снимок (доли функциональных зон по области и районам) считает бэкенд DSS по
    растру варианта; здесь он только сохраняется. Прямой вызов этой ручки через
    прокси закрыт — иначе клиент мог бы подложить произвольные доли.
    """
    variant: str
    snapshot: dict


class RejectBody(BaseModel):
    reason: str = ""
    rejected_by: Optional[UUID] = None


@router.patch("/{scenario_id}/parameters", response_model=ScenarioResponse)
async def patch_parameters(scenario_id: UUID, body: PatchParametersBody, db: AsyncSession = Depends(get_db)):
    """Обновляет управляющие параметры сценария без пересчёта KPI."""
    scenario = await _get_or_404(db, scenario_id)
    current = dict(scenario.parameters or {})
    current_params = current.get("scenario_params", {})
    update = body.model_dump(exclude_none=True)
    current_params.update(update)
    current["scenario_params"] = current_params
    scenario.parameters = current
    flag_modified(scenario, "parameters")
    await db.flush()
    await db.commit()
    return _to_response(await _get_or_404(db, scenario_id))


@router.put("/{scenario_id}/landscape", response_model=ScenarioResponse)
async def put_landscape(scenario_id: UUID, body: LandscapeLinkBody, db: AsyncSession = Depends(get_db)):
    """Привязывает к сценарию вариант ландшафтного планирования (снимок зонирования).

    Утверждённый или архивный сценарий не меняется: вариант ЛП фиксируется вместе
    с решением. Снимок попадает в контекст заключений ИИ, поэтому после смены
    варианта сохранённые заключения становятся устаревшими (другой input_hash).
    """
    if body.variant not in LANDSCAPE_VARIANTS or body.snapshot.get("variant") != body.variant:
        raise HTTPException(status_code=422, detail="Неизвестный вариант ландшафтного планирования")
    scenario = await _get_or_404(db, scenario_id)
    if scenario.status in ("approved", "archived"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Вариант ландшафтного планирования утверждённого сценария не меняется",
        )
    current = dict(scenario.parameters or {})
    current["landscape"] = body.snapshot
    scenario.parameters = current
    flag_modified(scenario, "parameters")
    await db.flush()
    await db.commit()
    return _to_response(await _get_or_404(db, scenario_id))


@router.post("/{scenario_id}/reject", response_model=ScenarioResponse)
async def reject_scenario(scenario_id: UUID, body: RejectBody, db: AsyncSession = Depends(get_db)):
    """Отклоняет сценарий с указанием причины."""
    scenario = await _get_or_404(db, scenario_id)
    current = dict(scenario.parameters or {})
    current["rejection_reason"] = body.reason
    if body.rejected_by:
        current["rejected_by"] = str(body.rejected_by)
    scenario.parameters = current
    flag_modified(scenario, "parameters")
    scenario.status = "rejected"
    await db.flush()
    await db.commit()
    return _to_response(await _get_or_404(db, scenario_id))


# ── LLM-narrative (autonomous local model) ──────────────────────────

# Промпты и посекционная генерация вынесены в app/services/narrative.py — тот же
# код использует офлайн-предгенерация на GPU-сервере, иначе промпты разъедутся и
# хэш входа перестанет совпадать.


def _direction_entry(s: DevelopmentScenario, direction_code: str) -> dict:
    """Запись нарратива конкретного направления (пустой dict, если ещё нет)."""
    return dict((s.narratives or {}).get(direction_code) or {})


def _is_fresh(s: DevelopmentScenario, direction_code: str, user_prompt: str) -> bool:
    """Готовый нарратив актуален, если отпечаток его входных данных совпадает с
    текущим (те же данные показателей, тот же сценарий, та же модель).

    Записи, сделанные до введения отпечатка, считаются актуальными: пересчитывать
    их принудительно нет причин, а ручной `force` по-прежнему доступен.
    """
    entry = _direction_entry(s, direction_code)
    stored = entry.get("input_hash")
    if not stored:
        return True
    return stored == narrative.input_hash(user_prompt)


def _direction_state(s: DevelopmentScenario, direction_code: str) -> str:
    """Статус нарратива направления: idle | running | done | failed."""
    return _direction_entry(s, direction_code).get("status") or "idle"


def _direction_response(s: DevelopmentScenario, direction_code: str) -> NarrativeStatusResponse:
    """Ответ поллинга по конкретному направлению из карты narratives."""
    entry = _direction_entry(s, direction_code)
    return NarrativeStatusResponse(
        scenario_id=s.id,
        direction_code=direction_code,
        narrative_status=entry.get("status") or "idle",
        narrative=entry.get("text"),
        narrative_generated_at=entry.get("generated_at"),
        narrative_error=entry.get("error"),
        model=settings.LLM_MODEL,
    )


async def _set_narrative_result(
    scenario_id: UUID,
    direction_code: str,
    *,
    status_: str,
    narrative: str | None = None,
    error: str | None = None,
    input_hash: str | None = None,
    source: str = "on-demand",
) -> None:
    """Записывает итог фоновой генерации в карту narratives по направлению
    (в отдельной сессии). Легаси-колонки narrative* обновляются на последнюю
    генерацию — для обратной совместимости старых потребителей."""
    async with AsyncSessionLocal() as db:
        sc = (
            await db.execute(
                select(DevelopmentScenario).where(DevelopmentScenario.id == scenario_id)
            )
        ).scalar_one_or_none()
        if sc is None:
            logger.warning("Нарратив: сценарий %s исчез до записи результата", scenario_id)
            return

        narratives = dict(sc.narratives or {})
        entry = dict(narratives.get(direction_code) or {})
        entry["status"] = status_
        if status_ == "done":
            entry["text"] = narrative
            entry["generated_at"] = datetime.now(timezone.utc).isoformat()
            entry["error"] = None
            entry["input_hash"] = input_hash
            entry["model"] = settings.LLM_MODEL
            entry["source"] = source
        elif status_ == "failed":
            entry["error"] = (error or "")[:1000]
        narratives[direction_code] = entry
        sc.narratives = narratives
        flag_modified(sc, "narratives")

        # Легаси-зеркало последней генерации.
        sc.narrative_status = status_
        if status_ == "done":
            sc.narrative = narrative
            sc.narrative_generated_at = datetime.now(timezone.utc)
            sc.narrative_error = None
        elif status_ == "failed":
            sc.narrative_error = (error or "")[:1000]
        await db.commit()


async def _run_narrative_generation(
    scenario_id: UUID, direction_code: str, user_prompt: str
) -> None:
    """Фоновая посекционная генерация нарратива (см. services/narrative.py) с
    записью результата и отпечатка входа в development_scenarios."""
    started = time.monotonic()
    try:
        narrative_text = await narrative.generate_sections(
            user_prompt,
            on_section=lambda idx, total, elapsed: logger.info(
                "Нарратив scenario=%s направление=%s: секция %d/%d готова (%.1f c)",
                scenario_id, direction_code, idx, total, elapsed,
            ),
        )
    except LLMUnavailableError as e:
        logger.warning("LLM unavailable (narrative scenario=%s): %s", scenario_id, e)
        await _set_narrative_result(
            scenario_id,
            direction_code,
            status_="failed",
            error=(
                "Автономная LLM не ответила вовремя. Повторите попытку. Если "
                "повторяется — проверьте канал к GPU-серверу и резервный inference."
            ),
        )
        return
    except Exception as e:  # noqa: BLE001 — фон не должен падать молча
        logger.error("Ошибка генерации нарратива scenario=%s: %s", scenario_id, e, exc_info=True)
        await _set_narrative_result(scenario_id, direction_code, status_="failed", error=str(e))
        return

    await _set_narrative_result(
        scenario_id, direction_code, status_="done", narrative=narrative_text,
        input_hash=narrative.input_hash(user_prompt),
    )
    logger.info(
        "Нарратив сгенерирован scenario=%s направление=%s за %.1f c",
        scenario_id, direction_code, time.monotonic() - started,
    )


def _narrative_state(s: DevelopmentScenario) -> str:
    """Текущий статус нарратива с дефолтами для старых записей."""
    if s.narrative_status:
        return s.narrative_status
    return "done" if s.narrative else "idle"


async def reset_orphaned_narratives() -> None:
    """Вызывается при старте сервиса. Фоновые задачи генерации не переживают
    рестарт процесса (asyncio-таски умирают вместе с ним), а их статус остаётся
    'running' → фронт будет вечно поллить мёртвую задачу, а перезапуск генерации
    для этого направления заблокирован. Помечаем такие нарративы как failed, чтобы
    их можно было запустить заново."""
    msg = "Генерация была прервана перезапуском сервиса. Запустите нарратив заново."
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(DevelopmentScenario))).scalars().all()
        changed = 0
        for sc in rows:
            touched = False
            narratives = dict(sc.narratives or {})
            for dc, entry in list(narratives.items()):
                if (entry or {}).get("status") == "running":
                    e = dict(entry)
                    e["status"] = "failed"
                    e["error"] = msg
                    narratives[dc] = e
                    touched = True
            if touched:
                sc.narratives = narratives
                flag_modified(sc, "narratives")
            if sc.narrative_status == "running":
                sc.narrative_status = "failed"
                sc.narrative_error = msg
                touched = True
            if touched:
                changed += 1
        if changed:
            await db.commit()
            logger.info(
                "Сброшено зависших нарративов (running→failed): %d сценариев", changed
            )


async def _mark_narrative_running(scenario_id: UUID, direction_code: str) -> None:
    """Помечает направление как running в отдельной сессии (для батча)."""
    async with AsyncSessionLocal() as db:
        sc = (
            await db.execute(
                select(DevelopmentScenario).where(DevelopmentScenario.id == scenario_id)
            )
        ).scalar_one_or_none()
        if sc is None:
            return
        narratives = dict(sc.narratives or {})
        entry = dict(narratives.get(direction_code) or {})
        entry["status"] = "running"
        entry["error"] = None
        narratives[direction_code] = entry
        sc.narratives = narratives
        flag_modified(sc, "narratives")
        sc.narrative_status = "running"
        sc.narrative_error = None
        await db.commit()


async def _run_narrative_batch(
    scenario_id: UUID, region_id: UUID, direction_codes: list[str], force: bool
) -> None:
    """Фоновая ПОСЛЕДОВАТЕЛЬНАЯ генерация по нескольким направлениям (по одному за
    раз — на CPU параллелизм не ускоряет, а при NUM_PARALLEL=1 ещё и встал бы в
    очередь). По каждому направлению обновляет свою запись, поэтому фронт видит
    прогресс ✓ по мере готовности."""
    for dc in direction_codes:
        # Промпт собираем в свежей сессии; пропускаем невалидные/уже готовые.
        async with AsyncSessionLocal() as db:
            sc = (
                await db.execute(
                    select(DevelopmentScenario).where(DevelopmentScenario.id == scenario_id)
                )
            ).scalar_one_or_none()
            if sc is None:
                return
            state = _direction_state(sc, dc)
            if state == "running":
                continue  # кто-то уже генерирует это направление
            try:
                ctx = await build_geo_context(db, direction_code=dc, region_id=region_id)
            except ValueError as e:
                await _set_narrative_result(scenario_id, dc, status_="failed", error=str(e))
                continue
            prompt = narrative.build_user_prompt_for(sc, render_context_as_text(ctx))
            # Готовое переделываем, только если входные данные изменились
            # (или явный force): сравниваем отпечаток промпта.
            if state == "done" and not force and _is_fresh(sc, dc, prompt):
                continue
            user_prompt = prompt

        await _mark_narrative_running(scenario_id, dc)
        # Переиспользуем посекционную генерацию (сама пишет done/failed).
        await _run_narrative_generation(scenario_id, dc, user_prompt)

    logger.info(
        "Батч-нарративы завершены scenario=%s (%d направлений)",
        scenario_id, len(direction_codes),
    )


@router.post("/{scenario_id}/narrative/all", status_code=status.HTTP_202_ACCEPTED)
async def start_all_narratives(
    scenario_id: UUID,
    body: GenerateAllNarrativesRequest,
    db: AsyncSession = Depends(get_db),
):
    """Запускает **последовательную** фоновую генерацию нарративов по списку
    направлений. Возвращает 202 сразу; готовность каждого направления опрашивается
    через GET .../narrative/status?direction_code=… или в общем ответе сценария
    (поле narratives)."""
    scenario = await _get_or_404(db, scenario_id)
    directions = [d.strip() for d in body.directions if d.strip()]
    if not directions:
        raise HTTPException(status_code=400, detail="Список направлений пуст")

    task = asyncio.create_task(
        _run_narrative_batch(scenario.id, scenario.region_id, directions, body.force)
    )
    _NARRATIVE_TASKS.add(task)
    task.add_done_callback(_NARRATIVE_TASKS.discard)

    return {"scenario_id": str(scenario.id), "queued": directions, "force": body.force}


@router.post(
    "/{scenario_id}/narrative",
    response_model=NarrativeStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_narrative(
    scenario_id: UUID,
    body: GenerateNarrativeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Запускает **асинхронную** генерацию нарратива локальной автономной LLM
    (Ollama, OpenAI-совместимый API; весь трафик внутри docker-compose сети).
    Возвращает 202 со статусом `running` — готовность опрашивается через
    GET /scenarios/{id}/narrative/status. Долгий шаг (LLM на CPU до ~6 мин)
    выполняется в фоне, поэтому HTTP-запрос не висит."""
    scenario = await _get_or_404(db, scenario_id)

    # Валидация направления и сбор гео-контекста — синхронно (быстрый SQL),
    # чтобы вернуть понятный 400 при неверном направлении до запуска фона.
    try:
        ctx = await build_geo_context(
            db, direction_code=body.direction_code, region_id=scenario.region_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    direction_code = body.direction_code

    # Уже идёт по ЭТОМУ направлению — не плодим вторую генерацию. Генерация по
    # другому направлению допускается параллельно (у каждого своя запись).
    if _direction_state(scenario, direction_code) == "running":
        return _direction_response(scenario, direction_code)

    geo_text = render_context_as_text(ctx)
    user_prompt = narrative.build_user_prompt_for(scenario, geo_text)

    # Готовый нарратив с совпавшим отпечатком входа отдаём немедленно, к LLM не
    # обращаясь: это и делает открытие сценария мгновенным, и оставляет систему
    # работоспособной при обрыве канала к GPU-серверу.
    if (
        not getattr(body, "force", False)
        and _direction_state(scenario, direction_code) == "done"
        and _is_fresh(scenario, direction_code, user_prompt)
    ):
        return _direction_response(scenario, direction_code)

    # Помечаем направление как running в карте narratives (+ легаси-зеркало).
    narratives = dict(scenario.narratives or {})
    entry = dict(narratives.get(direction_code) or {})
    entry["status"] = "running"
    entry["error"] = None
    narratives[direction_code] = entry
    scenario.narratives = narratives
    flag_modified(scenario, "narratives")
    scenario.narrative_status = "running"
    scenario.narrative_error = None
    await db.commit()

    # Фоновая корутина переживает ответ (один процесс uvicorn).
    task = asyncio.create_task(
        _run_narrative_generation(scenario_id, direction_code, user_prompt)
    )
    _NARRATIVE_TASKS.add(task)
    task.add_done_callback(_NARRATIVE_TASKS.discard)

    return _direction_response(scenario, direction_code)


@router.get("/{scenario_id}/narrative/status", response_model=NarrativeStatusResponse)
async def narrative_status(
    scenario_id: UUID,
    direction_code: Optional[str] = Query(
        None, description="Направление; без него — легаси-статус последней генерации"
    ),
    db: AsyncSession = Depends(get_db),
):
    """Текущий статус генерации нарратива (для поллинга фронтом).

    С `direction_code` возвращает нарратив конкретного направления из карты
    narratives. Без него — легаси-поведение (последняя генерация)."""
    s = await _get_or_404(db, scenario_id)
    if direction_code:
        return _direction_response(s, direction_code)
    return NarrativeStatusResponse(
        scenario_id=s.id,
        narrative_status=_narrative_state(s),
        narrative=s.narrative,
        narrative_generated_at=s.narrative_generated_at,
        narrative_error=s.narrative_error,
        model=settings.LLM_MODEL,
    )
