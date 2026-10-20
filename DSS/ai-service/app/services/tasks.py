import logging
from datetime import date
from uuid import UUID

from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_models import DevelopmentScenario, RegionalTask

logger = logging.getLogger(__name__)

# Шаблоны задач с привязкой к конкретным районам СКО (поле district_code).
# Качественная задача (управленческое действие) + опциональный kpi_key: если он
# задан и у утверждённого сценария есть такой KPI, target_value/unit берутся из
# реального KPI сценария (а не из захардкоженного числа), а в описание пишется
# областная цель. Так задачи привязаны к рассчитанным по данным показателям.
TASK_TEMPLATES: dict[str, list[dict]] = {
    "optimal": [
        {"title": "Строительство новых автодорог", "category": "Инфраструктура", "target_metric": "км_дорог", "target_value": 50.0, "unit": "км", "district_code": "SKO-KYZYL"},
        {"title": "Развитие агропромышленного кластера", "category": "АПК", "target_metric": "валовая_продукция_СХ", "kpi_key": "agri_output", "target_value": 15000.0, "unit": "млн тг", "district_code": "SKO-AIYR"},
        {"title": "Цифровизация государственных услуг", "category": "Цифровизация", "target_metric": "процент_онлайн", "target_value": 85.0, "unit": "%", "district_code": "SKO-PETRO"},
        {"title": "Расширение медицинской инфраструктуры", "category": "Здравоохранение", "target_metric": "коек_на_1000", "target_value": 8.5, "unit": "коек/1000", "district_code": "SKO-MAMLY"},
        {"title": "Привлечение инвестиций в АПК", "category": "Экономика", "target_metric": "инвестиции_в_АПК", "kpi_key": "agri_investment", "target_value": 12000.0, "unit": "ед.", "district_code": "SKO-AKKAY"},
    ],
    "stable": [
        {"title": "Техническое обслуживание дорожной инфраструктуры", "category": "Инфраструктура", "target_metric": "км_отремонтировано", "target_value": 30.0, "unit": "км", "district_code": "SKO-ZHAMB"},
        {"title": "Поддержка действующих предприятий АПК", "category": "АПК", "target_metric": "валовая_продукция_СХ", "kpi_key": "agri_output", "target_value": 5000.0, "unit": "чел.", "district_code": "SKO-ZHUMB"},
        {"title": "Модернизация системы ЖКХ", "category": "ЖКХ", "target_metric": "процент_охвата", "target_value": 78.0, "unit": "%", "district_code": "SKO-SHALA"},
    ],
    "risk": [
        {"title": "Антикризисный план занятости населения", "category": "Занятость", "target_metric": "снижение_безработицы", "target_value": 2.0, "unit": "проц.пп.", "district_code": "SKO-AKZHAR"},
        {"title": "Ревизия и оптимизация бюджетных расходов", "category": "Финансы", "target_metric": "экономия_бюджета", "target_value": 500.0, "unit": "млн тг", "district_code": "SKO-TIMUR"},
        {"title": "Оптимизация государственных закупок", "category": "Госзакупки", "target_metric": "снижение_стоимости", "target_value": 10.0, "unit": "%", "district_code": "SKO-UALI"},
        {"title": "Антикризисная поддержка АПК", "category": "Экономика", "target_metric": "инвестиции_в_АПК", "kpi_key": "agri_investment", "target_value": 8000.0, "unit": "ед.", "district_code": "SKO-PETRO"},
    ],
    "custom": [],
}


async def _resolve_district_ids(db: AsyncSession, codes: list[str]) -> dict[str, UUID]:
    """Получает словарь {region_code: region_id} для указанных кодов районов."""
    if not codes:
        return {}
    result = await db.execute(
        text("SELECT id, code FROM regions WHERE code = ANY(:codes)"),
        {"codes": codes},
    )
    return {row.code: row.id for row in result.fetchall()}


async def generate_tasks_from_scenario(
    db: AsyncSession, scenario_id: UUID, require_approved: bool = True
) -> list[RegionalTask]:
    """
    Генерирует RegionalTask из сценария.
    Каждая задача привязывается к конкретному району (district_code в шаблоне).
    При require_approved=True сценарий должен иметь статус 'approved'.
    """
    result = await db.execute(select(DevelopmentScenario).where(DevelopmentScenario.id == scenario_id))
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise ValueError(f"Сценарий {scenario_id} не найден")
    if require_approved and scenario.status != "approved":
        raise ValueError(f"Генерация задач возможна только для одобренных сценариев. Статус: {scenario.status}")

    templates = TASK_TEMPLATES.get(scenario.scenario_type, [])
    if not templates:
        return []

    # KPI утверждённого сценария (рассчитаны из данных) — источник target_value
    # для задач, у которых задан kpi_key.
    kpis = (scenario.parameters or {}).get("kpis", {})
    kpi_meta = (scenario.parameters or {}).get("kpi_meta", {})

    # Резолвим коды районов в UUID
    district_codes = list({tmpl.get("district_code") for tmpl in templates if tmpl.get("district_code")})
    district_id_map = await _resolve_district_ids(db, district_codes)

    deadline = date(scenario.target_year, 12, 31)
    tasks = []
    for tmpl in templates:
        district_code = tmpl.get("district_code")
        region_id = district_id_map.get(district_code) if district_code else None
        # Fallback на регион сценария (область) если район не найден
        if region_id is None:
            region_id = scenario.region_id

        target_value = tmpl.get("target_value")
        unit = tmpl.get("unit")
        description = None
        kpi_key = tmpl.get("kpi_key")
        if kpi_key and kpi_key in kpis:
            # target из реального KPI сценария (а не из константы шаблона)
            target_value = kpis[kpi_key]
            meta = kpi_meta.get(kpi_key, {})
            unit = meta.get("unit", unit)
            description = (
                f"Вклад района в областную цель сценария «{scenario.name}»: "
                f"{meta.get('label', kpi_key)} = {target_value} {unit} "
                f"(целевой {scenario.target_year} год)."
            )

        task = RegionalTask(
            scenario_id=scenario.id,
            region_id=region_id,
            title=tmpl["title"],
            description=description,
            category=tmpl.get("category"),
            target_metric=tmpl.get("target_metric"),
            target_value=target_value,
            unit=unit,
            deadline=deadline,
            status="pending",
        )
        db.add(task)
        tasks.append(task)

    await db.flush()
    for task in tasks:
        await db.refresh(task)

    logger.info("Создано %d задач для сценария %s", len(tasks), scenario_id)
    return tasks


async def get_tasks_by_scenario(db: AsyncSession, scenario_id: UUID) -> list[RegionalTask]:
    result = await db.execute(
        select(RegionalTask).where(RegionalTask.scenario_id == scenario_id).order_by(RegionalTask.created_at.asc())
    )
    return list(result.scalars().all())


async def get_tasks_by_region(
    db: AsyncSession,
    region_id: UUID,
    status: str | None = None,
) -> list[RegionalTask]:
    conditions = [RegionalTask.region_id == region_id]
    if status is not None:
        conditions.append(RegionalTask.status == status)
    result = await db.execute(
        select(RegionalTask).where(and_(*conditions)).order_by(RegionalTask.deadline.asc().nullslast())
    )
    return list(result.scalars().all())


async def update_task_status(
    db: AsyncSession,
    task_id: UUID,
    status: str,
    completion_report: str | None = None,
) -> RegionalTask:
    valid = frozenset({"pending", "in_progress", "completed", "cancelled", "accepted"})
    if status not in valid:
        raise ValueError(f"Недопустимый статус: {status}. Допустимые: {valid}")
    result = await db.execute(select(RegionalTask).where(RegionalTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise ValueError(f"Задача {task_id} не найдена")
    task.status = status
    if completion_report is not None:
        task.completion_report = completion_report
    await db.flush()
    await db.refresh(task)
    return task
