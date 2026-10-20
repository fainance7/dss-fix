"""
Internal integration endpoints for the main backend to call.
All endpoints require X-AI-Key header authentication.
"""
import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.ai_models import DevelopmentScenario, Forecast, RegionalTask
from app.schemas.task import TaskResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])

_api_key_header = APIKeyHeader(name="X-AI-Key", auto_error=False)


async def verify_internal_key(api_key: str | None = Security(_api_key_header)) -> str:
    if api_key != settings.AI_SECRET_KEY:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недействительный X-AI-Key")
    return api_key


class ForecastRequestBody(BaseModel):
    model_id: UUID
    indicator_id: UUID | None = None
    region_id: UUID
    horizon_years: int | None = None
    horizon_months: int | None = None  # устарело: трактуется как годы
    triggered_by: UUID | None = None


class ForecastRequestResponse(BaseModel):
    forecast_id: UUID
    model_id: UUID
    region_id: UUID
    status: str
    summary: dict


class ScenarioApproveBody(BaseModel):
    scenario_id: UUID
    approved_by: UUID
    # Имя утвердившего подставляет backend-прокси из учётной записи: в ai-service
    # нет справочника пользователей, а карточка сценария показывает, кто утвердил (ФFE.4).
    approved_by_name: str | None = Field(default=None, max_length=200)
    # Комментарий к решению акима области — сохраняется в параметрах сценария.
    comment: str | None = Field(default=None, max_length=2000)


class ScenarioApproveResponse(BaseModel):
    scenario_id: UUID
    status: str
    tasks_count: int
    tasks: list[TaskResponse]


class RegionSummaryResponse(BaseModel):
    region_id: UUID
    latest_forecast: dict | None
    active_scenarios: int
    task_stats: dict[str, int]
    calculated_indicators: dict


@router.post("/forecast-request", response_model=ForecastRequestResponse, status_code=status.HTTP_201_CREATED)
async def forecast_request(
    body: ForecastRequestBody,
    db: AsyncSession = Depends(get_db),
    _key: str = Depends(verify_internal_key),
):
    """Запускает прогноз по запросу основного бэкенда."""
    from app.services.forecasting import run_forecast
    try:
        forecast = await run_forecast(
            db=db, model_id=body.model_id, region_id=body.region_id,
            horizon_years=body.horizon_years, horizon_months=body.horizon_months,
            triggered_by=body.triggered_by,
            indicator_id=body.indicator_id,
        )
        await db.commit()
        await db.refresh(forecast)
        result_values = forecast.result_values or {}
        return ForecastRequestResponse(
            forecast_id=forecast.id, model_id=forecast.model_id, region_id=body.region_id,
            status=forecast.status,
            summary={
                "forecast_date": str(forecast.forecast_date),
                "target_date_from": str(forecast.target_date_from),
                "target_date_to": str(forecast.target_date_to),
                "values_count": len(result_values),
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        logger.error("Ошибка запуска прогноза: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Ошибка прогноза")


@router.post("/scenario-approve", response_model=ScenarioApproveResponse)
async def scenario_approve(
    body: ScenarioApproveBody,
    db: AsyncSession = Depends(get_db),
    _key: str = Depends(verify_internal_key),
):
    """Утверждает сценарий и запускает генерацию задач."""
    from app.services.tasks import generate_tasks_from_scenario

    result = await db.execute(select(DevelopmentScenario).where(DevelopmentScenario.id == body.scenario_id))
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Сценарий {body.scenario_id} не найден")
    if scenario.status == "approved":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Сценарий уже утверждён")

    # Archive any previously approved scenario for the same region+year
    prev_approved = await db.execute(
        select(DevelopmentScenario).where(
            DevelopmentScenario.region_id == scenario.region_id,
            DevelopmentScenario.target_year == scenario.target_year,
            DevelopmentScenario.status == "approved",
            DevelopmentScenario.id != scenario.id,
        )
    )
    for prev in prev_approved.scalars().all():
        prev.status = "archived"

    # Archive all other non-archived scenarios from the same generation group
    siblings = await db.execute(
        select(DevelopmentScenario).where(
            DevelopmentScenario.region_id == scenario.region_id,
            DevelopmentScenario.target_year == scenario.target_year,
            DevelopmentScenario.id != scenario.id,
            DevelopmentScenario.status.notin_(["approved", "archived"]),
        )
    )
    sibling_ids = []
    for sib in siblings.scalars().all():
        sib.status = "archived"
        sibling_ids.append(sib.id)

    # Черновые задачи отклонённых вариантов этого поколения (созданы при генерации
    # для предпросмотра) снимаются — иначе висят у акимов в «Мероприятиях».
    if sibling_ids:
        drafts = await db.execute(
            select(RegionalTask).where(
                RegionalTask.scenario_id.in_(sibling_ids), RegionalTask.status == "pending",
            )
        )
        for t in drafts.scalars().all():
            t.status = "cancelled"

    scenario.status = "approved"
    scenario.approved_by = body.approved_by
    scenario.approved_at = datetime.now(timezone.utc)
    updates = {}
    if body.comment and body.comment.strip():
        updates["approval_comment"] = body.comment.strip()
    if body.approved_by_name:
        updates["approved_by_name"] = body.approved_by_name
    if updates:
        scenario.parameters = {**(scenario.parameters or {}), **updates}
        flag_modified(scenario, "parameters")
    await db.flush()

    # Задачи сценария уже созданы при генерации (и могли быть отредактированы
    # экспертом в «Задачах по сценарию») — они и становятся рабочими. Повторная
    # генерация из шаблонов давала дубли каждой задачи.
    existing = (
        await db.execute(select(RegionalTask).where(RegionalTask.scenario_id == scenario.id))
    ).scalars().all()
    try:
        tasks = list(existing) if existing else await generate_tasks_from_scenario(db, body.scenario_id)
        await db.commit()
        for t in tasks:
            await db.refresh(t)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    return ScenarioApproveResponse(
        scenario_id=body.scenario_id, status="approved",
        tasks_count=len(tasks), tasks=[TaskResponse.model_validate(t) for t in tasks],
    )


@router.get("/region-summary/{region_id}", response_model=RegionSummaryResponse)
async def region_summary(
    region_id: UUID,
    db: AsyncSession = Depends(get_db),
    _key: str = Depends(verify_internal_key),
):
    """Возвращает последние KPI региона: прогноз, сценарии, задачи, показатели."""
    # Последний прогноз
    fc_result = await db.execute(
        select(Forecast).where(Forecast.region_id == region_id).order_by(Forecast.created_at.desc()).limit(1)
    )
    fc = fc_result.scalar_one_or_none()
    latest_forecast = None
    if fc:
        latest_forecast = {
            "id": str(fc.id), "model_id": str(fc.model_id),
            "forecast_date": str(fc.forecast_date), "status": fc.status,
            "values_count": len(fc.result_values or {}),
        }

    # Активные сценарии
    sc_count = await db.execute(
        select(func.count(DevelopmentScenario.id)).where(
            and_(DevelopmentScenario.region_id == region_id, DevelopmentScenario.status.in_(["calculated", "approved"]))
        )
    )
    active_scenarios = sc_count.scalar() or 0

    # Статистика задач
    task_stats: dict[str, int] = {}
    for s in ("pending", "in_progress", "completed", "cancelled", "accepted"):
        cnt = await db.execute(
            select(func.count(RegionalTask.id)).where(and_(RegionalTask.region_id == region_id, RegionalTask.status == s))
        )
        task_stats[s] = cnt.scalar() or 0

    return RegionSummaryResponse(
        region_id=region_id, latest_forecast=latest_forecast,
        active_scenarios=int(active_scenarios), task_stats=task_stats,
        calculated_indicators={},
    )
