"""API алгоритма ЗБ-УР: земельный баланс → устойчивое развитие.

Доступ — через существующий RBAC-прокси бэкенда (`/api/v1/ai/*`), который серверно
подставляет X-AI-Key. Новых механизмов авторизации модуль не вводит.

Правило вывода, обязательное для всех эндпоинтов: точечная оценка без интервала
наружу не отдаётся, и рядом с каждым числом идёт метка типа связи [Б]/[С]/[Э].
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal, get_db
from app.models.ai_models import LandscapeImpactRun
from app.schemas.landscape_impact import (
    AssumptionsResponse,
    CalibrationResponse,
    CompareResponse,
    ExpressEstimate,
    RunCreateRequest,
    RunDetail,
    RunSummary,
    ScenarioRequest,
)
from app.services.landscape_impact import sd_index, uncertainty, verdict as verdict_mod
from app.services.landscape_impact.assumptions import (
    assumptions_version,
    list_assumptions,
    unverified_keys,
)
from app.services.landscape_impact.calibration import calibrate
from app.services.landscape_impact.datamart import load_datamart, reference_version
from app.services.landscape_impact.pipeline import (
    ALGORITHM_VERSION,
    ScenarioInput,
    estimate_express,
    run_full_assessment,
    variant_presets,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/landscape-impact", tags=["landscape-impact"])

# Удерживаем ссылки на фоновые прогоны: asyncio.create_task не держит strong-ссылку.
_RUN_TASKS: set = set()


def _to_input(body: ScenarioRequest) -> ScenarioInput:
    return ScenarioInput(
        name=body.name,
        target_areas=body.target_areas,
        class_shares=body.class_shares,
        class_map=body.class_map,
        horizon_years=body.horizon_years,
        suitability_shift=body.suitability_shift,
        coverage=body.coverage,
        base_year=body.base_year,
        land_use_shift=body.land_use_shift,
        variant_code=body.variant_code,
    )


@router.get("/assumptions", response_model=AssumptionsResponse)
async def get_assumptions() -> AssumptionsResponse:
    """Действующий набор экспертных коэффициентов с диапазонами и источниками.

    Эндпоинт существует ради проверяемости: пользователь, читающий результат,
    должен иметь возможность увидеть, какие величины назначены экспертно и на
    каком основании. Скрытые константы в расчёте не допускаются.
    """
    items = [a.as_dict() for a in list_assumptions().values()]
    return AssumptionsResponse(
        version=assumptions_version(),
        algorithm_version=ALGORITHM_VERSION,
        dss_index_version=sd_index.index_version(),
        assumptions=items,
        unverified=unverified_keys(),
        dss_index_definition=sd_index.index_definition(),
    )


@router.get("/variant-presets")
async def get_variant_presets() -> dict:
    """Сдвиг угодий по умолчанию для вариантов ЛП V1–V3 (вход `land_use_shift`).

    Значения посчитаны наложением растров вариантов на маски категорий земель
    смежной команды; правило перевода — экспертное допущение [Э], оно отдаётся
    вместе с числами, чтобы интерфейс показывал его рядом с полями ввода.
    """
    return variant_presets()


@router.get("/calibration", response_model=CalibrationResponse)
async def get_calibration(db: AsyncSession = Depends(get_db)) -> CalibrationResponse:
    """Результат калибровки алгоритма на историческом окне 2014-2024.

    Это шаг, который заказчик описал как «на этом примере вырабатываем алгоритмы»:
    что именно извлечено из истории, какие связи прошли проверку, а какие
    понижены до экспертных, и каков минимальный содержательный эффект.
    """
    mart = await load_datamart(db)
    calib = calibrate(mart)
    data = calib.as_dict()
    return CalibrationResponse(
        **{k: data[k] for k in (
            "window", "balance_ok", "structural_shares", "yield_link",
            "yield_candidates", "yield_candidates_oblast", "direct_land_links", "sd_aggregation", "mde",
            "vds_share_observed", "notes")},
        versions={
            "algorithm": ALGORITHM_VERSION,
            "assumptions": assumptions_version(),
            "datamart": reference_version(),
            "dss_index": sd_index.index_version(),
        },
        data_issues=list(mart.issues),
    )


@router.post("/estimate", response_model=ExpressEstimate)
async def estimate(body: ScenarioRequest, db: AsyncSession = Depends(get_db)) -> ExpressEstimate:
    """Экспресс-оценка: вариант + горизонт → дельты ВРП и валовой продукции СХ.

    Синхронно, аналитическая свёртка диапазонов вместо полного Монте-Карло.
    Отвечает на второй вопрос заказчика — быстрая оценка влияния на рост экономики.
    """
    mart = await load_datamart(db)
    try:
        result = estimate_express(_to_input(body), mart=mart)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=str(exc)) from exc
    return ExpressEstimate(**result)


@router.post("/runs", response_model=RunDetail, status_code=status.HTTP_202_ACCEPTED)
async def create_run(body: RunCreateRequest, db: AsyncSession = Depends(get_db)) -> RunDetail:
    """Полный расчёт: вся цепь, Монте-Карло, индексы УР, гейты, вердикт.

    Асинхронно: 202 и идентификатор прогона. Результат забирается по
    `GET /runs/{id}` вместе со статусом каждого гейта.
    """
    mart = await load_datamart(db)
    run = LandscapeImpactRun(
        name=body.name,
        variant_code=body.variant_code,
        region_id=body.region_id,
        base_year=body.base_year or mart.years[-1],
        horizon_years=body.horizon_years,
        scenario_input=body.model_dump(mode="json", exclude={"created_by", "region_id"}),
        status="pending",
        algorithm_version=ALGORITHM_VERSION,
        assumptions_version=assumptions_version(),
        datamart_version=reference_version(),
        random_seed=body.seed,
        draws=body.draws,
        created_by=body.created_by,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    task = asyncio.create_task(_execute_run(run.id, body))
    _RUN_TASKS.add(task)
    task.add_done_callback(_RUN_TASKS.discard)
    return _to_detail(run)


async def _execute_run(run_id: UUID, body: RunCreateRequest) -> None:
    """Фоновое выполнение прогона в собственной сессии БД."""
    async with AsyncSessionLocal() as session:
        run = await session.get(LandscapeImpactRun, run_id)
        if run is None:
            return
        run.status = "running"
        await session.commit()

        try:
            mart = await load_datamart(session)
            # Расчёт синхронный и CPU-bound; уводим в поток, чтобы не блокировать
            # событийный цикл сервиса на время Монте-Карло.
            result = await asyncio.to_thread(
                run_full_assessment, _to_input(body),
                mart=mart, draws=body.draws, seed=body.seed,
            )
            payload = result.as_dict()
            run.result = payload
            run.gate_status = payload["gates"]["status"]
            run.verdict = payload["verdict"]["code"]
            run.status = "completed"
        except Exception as exc:  # noqa: BLE001 — ошибка прогона не должна ронять сервис
            logger.exception("Прогон ЗБ-УР %s завершился ошибкой", run_id)
            run.status = "failed"
            run.error = str(exc)
        finally:
            run.completed_at = datetime.now(timezone.utc)
            await session.commit()


@router.get("/runs", response_model=list[RunSummary])
async def list_runs(
    variant_code: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[RunSummary]:
    """Список прогонов, новые сверху."""
    query = select(LandscapeImpactRun).order_by(LandscapeImpactRun.created_at.desc()).limit(limit)
    if variant_code:
        query = query.where(LandscapeImpactRun.variant_code == variant_code)
    rows = (await db.execute(query)).scalars().all()
    return [RunSummary.model_validate(r) for r in rows]


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: UUID, db: AsyncSession = Depends(get_db)) -> RunDetail:
    """Результат прогона и статус каждого гейта."""
    run = await db.get(LandscapeImpactRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Прогон {run_id} не найден")
    return _to_detail(run)


@router.get("/compare", response_model=CompareResponse)
async def compare_runs(
    ids: str = Query(description="Идентификаторы прогонов через запятую"),
    db: AsyncSession = Depends(get_db),
) -> CompareResponse:
    """Попарное сравнение вариантов по распределениям разностей.

    Собственного рейтинга вариантов модуль не строит: выбор — управленческое
    решение. Варианты, различие которых меньше ширины интервала, объявляются
    неразличимыми, а не ранжируются по точечным оценкам.
    """
    try:
        run_ids = [UUID(x.strip()) for x in ids.split(",") if x.strip()]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Некорректный идентификатор прогона") from exc
    if len(run_ids) < 2:
        raise HTTPException(status_code=422, detail="Нужно минимум два прогона для сравнения")

    rows = (await db.execute(
        select(LandscapeImpactRun).where(LandscapeImpactRun.id.in_(run_ids))
    )).scalars().all()
    completed = [r for r in rows if r.status == "completed" and r.result]
    if len(completed) < 2:
        raise HTTPException(status_code=422,
                            detail="Для сравнения нужно минимум два завершённых прогона")

    pairs = []
    for i in range(len(completed)):
        for j in range(i + 1, len(completed)):
            a, b = completed[i], completed[j]
            pairs.append(_compare_summaries(a, b))
    return CompareResponse(
        pairs=pairs,
        note=(
            "Сравнение ведётся по распределениям разностей, а не по ранжированию "
            "точечных оценок. Модуль не строит собственного рейтинга вариантов: "
            "выбор варианта — управленческое решение, модуль даёт матрицу последствий."
        ),
    )


def _compare_summaries(a: LandscapeImpactRun, b: LandscapeImpactRun) -> dict:
    """Сравнение двух прогонов по опубликованным характеристикам распределений.

    Выборки Монте-Карло в результате не хранятся (это десятки тысяч чисел на
    прогон), поэтому сравнение строится на медианах и интервалах: пересечение
    80 % интервалов означает неразличимость вариантов.
    """
    da = a.result["sd_index"]["deltas"]["dss_total"]
    db_ = b.result["sd_index"]["deltas"]["dss_total"]
    lo_a, hi_a = da["ci80"]
    lo_b, hi_b = db_["ci80"]
    overlap = not (hi_a < lo_b or hi_b < lo_a)
    better = a.name if da["median"] > db_["median"] else b.name
    return {
        "variant_a": a.name, "variant_b": b.name,
        "run_a": str(a.id), "run_b": str(b.id),
        "delta_a": da, "delta_b": db_,
        "median_difference": round(da["median"] - db_["median"], 6),
        "intervals_overlap": overlap,
        "indistinguishable": overlap,
        "conclusion": (
            f"Различия между вариантами «{a.name}» и «{b.name}» не подтверждены: "
            f"80 % интервалы пересекаются."
            if overlap else
            f"Вариант «{better}» предпочтительнее по итоговому индексу УР: "
            f"80 % интервалы не пересекаются."
        ),
        "gate_status_a": a.gate_status, "gate_status_b": b.gate_status,
        "caveat": (
            "Хотя бы один из прогонов не допущен на дашборд по гейтам валидации — "
            "сравнение носит исследовательский характер."
            if "RESEARCH_ONLY" in {a.gate_status, b.gate_status} else None
        ),
    }


def _to_detail(run: LandscapeImpactRun) -> RunDetail:
    return RunDetail(
        id=run.id, name=run.name, variant_code=run.variant_code,
        base_year=run.base_year, horizon_years=run.horizon_years,
        status=run.status, gate_status=run.gate_status, verdict=run.verdict,
        algorithm_version=run.algorithm_version, created_at=run.created_at,
        completed_at=run.completed_at, scenario_input=run.scenario_input,
        result=run.result, error=run.error,
        assumptions_version=run.assumptions_version,
        datamart_version=run.datamart_version,
        random_seed=run.random_seed, draws=run.draws,
    )
