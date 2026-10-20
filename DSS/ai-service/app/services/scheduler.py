import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import and_, select, text

from app.database import AsyncSessionLocal
from app.models.ai_models import AIModel, CalculatedIndicator, DevelopmentScenario

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Almaty")


def setup_scheduler(app) -> None:
    """Настраивает и прикрепляет планировщик к событиям жизненного цикла FastAPI."""

    @app.on_event("startup")
    async def start_scheduler() -> None:
        scheduler.add_job(
            run_daily_forecasts,
            trigger=CronTrigger(hour=2, minute=0, timezone="Asia/Almaty"),
            id="daily_forecasts",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        scheduler.add_job(
            run_weekly_indicators,
            trigger=CronTrigger(day_of_week="sun", hour=3, minute=0, timezone="Asia/Almaty"),
            id="weekly_indicators",
            replace_existing=True,
            misfire_grace_time=7200,
        )
        scheduler.add_job(
            run_scenario_refresh,
            trigger=CronTrigger(day_of_week="sat", hour=4, minute=0, timezone="Asia/Almaty"),
            id="scenario_refresh",
            replace_existing=True,
            misfire_grace_time=7200,
        )
        scheduler.start()
        logger.info("Планировщик задач запущен (Asia/Almaty)")

    @app.on_event("shutdown")
    async def stop_scheduler() -> None:
        if scheduler.running:
            scheduler.shutdown(wait=False)
            logger.info("Планировщик задач остановлен")


async def run_daily_forecasts() -> None:
    """
    Ежедневное задание (02:00 Алматы):
    Находит все активные модели и запускает прогнозы для всех районов.
    """
    from app.services.forecasting import run_forecast

    logger.info("Запуск ежедневных прогнозов: %s", datetime.now(timezone.utc).isoformat())
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(select(AIModel).where(AIModel.status == "active"))
            active_models = result.scalars().all()
            if not active_models:
                logger.info("Активных моделей не найдено")
                return

            regions_result = await db.execute(text("SELECT id FROM regions WHERE level = 2"))
            region_ids = [row[0] for row in regions_result.fetchall()]

            ok, fail = 0, 0
            for model in active_models:
                for region_id in region_ids:
                    try:
                        await run_forecast(db=db, model_id=model.id, region_id=region_id)
                        ok += 1
                    except Exception as exc:
                        fail += 1
                        logger.error("Ошибка прогноза model=%s region=%s: %s", model.id, region_id, exc)

            await db.commit()
            logger.info("Ежедневные прогнозы: успешно=%d, ошибок=%d", ok, fail)
        except Exception as exc:
            await db.rollback()
            logger.error("Критическая ошибка run_daily_forecasts: %s", exc)


async def run_weekly_indicators() -> None:
    """
    Еженедельное задание (воскресенье 03:00):
    Пересчитывает производные показатели из РЕАЛЬНЫХ данных `indicator_values`
    (ФИИ.1). Для каждого региона и базового показателя сохраняет: последнее
    одобренное значение (снимок) и прирост год-к-году (%). Синтетика (random)
    больше не используется.
    """
    from collections import defaultdict
    from datetime import date

    # Базовые показатели с реальными рядами по СКО (АПК + демография + климат).
    source_indicators = [
        "Валовая продукция сельского хозяйства",
        "Численность населения (всего)",
        "Урожайность зерновых культур",
        "Годовая сумма осадков",
        "Среднегодовая температура воздуха",
    ]

    logger.info("Запуск пересчёта показателей: %s", datetime.now(timezone.utc).isoformat())
    async with AsyncSessionLocal() as db:
        try:
            today = date.today()
            count = 0
            for name in source_indicators:
                # Показатель по имени (берём наиболее заполненный ряд при дублях).
                row = (await db.execute(text("""
                    SELECT i.id, u.name AS unit
                    FROM indicators i
                    JOIN units u ON u.id = i.unit_id
                    LEFT JOIN indicator_values iv
                           ON iv.indicator_id = i.id AND iv.validation_status = 'approved'
                    WHERE i.name = :name
                    GROUP BY i.id, u.name
                    ORDER BY COUNT(iv.id) DESC
                    LIMIT 1
                """), {"name": name})).first()
                if row is None:
                    logger.warning("Показатель не найден: %s", name)
                    continue
                indicator_id, unit = row[0], row[1]

                rows = (await db.execute(text("""
                    SELECT region_id, EXTRACT(YEAR FROM period_start)::int AS yr, value
                    FROM indicator_values
                    WHERE indicator_id = :iid AND validation_status = 'approved'
                      AND period_type = 'year' AND value IS NOT NULL
                    ORDER BY region_id, yr
                """), {"iid": str(indicator_id)})).fetchall()

                by_region: dict = defaultdict(list)
                for r in rows:
                    by_region[r[0]].append((int(r[1]), float(r[2])))

                for region_id, series in by_region.items():
                    series.sort()
                    last_year, latest = series[-1]
                    db.add(CalculatedIndicator(
                        name=f"{name} (последнее)",
                        formula_description=f"Последнее одобренное значение из indicator_values за {last_year}",
                        region_id=region_id,
                        indicator_date=today,
                        value=round(latest, 6),
                        unit=unit,
                        parameters={"source": "scheduler_weekly", "kind": "snapshot", "year": last_year},
                    ))
                    count += 1

                    if len(series) >= 2:
                        prev_year, prev = series[-2]
                        growth = (latest - prev) / abs(prev) * 100.0 if prev != 0 else None
                        # Защита от неоднородности единиц в исходных рядах СКО
                        # (напр. значение то в тыс., то в абсолюте): скачок >500%
                        # почти наверняка смена единицы, а не реальный прирост.
                        if growth is not None and abs(growth) <= 500.0:
                            db.add(CalculatedIndicator(
                                name=f"{name} — прирост г/г",
                                formula_description=f"(v[{last_year}] − v[{prev_year}]) / |v[{prev_year}]| × 100%",
                                region_id=region_id,
                                indicator_date=today,
                                value=round(growth, 6),
                                unit="%",
                                parameters={"source": "scheduler_weekly", "kind": "yoy_growth",
                                            "years": [prev_year, last_year]},
                            ))
                            count += 1

            await db.commit()
            logger.info("Показатели пересчитаны из реальных данных: создано=%d", count)
        except Exception as exc:
            await db.rollback()
            logger.error("Критическая ошибка run_weekly_indicators: %s", exc)


async def run_scenario_refresh() -> None:
    """
    Еженедельное задание (суббота 04:00):
    Перезапускает calculate_scenario() для черновых сценариев старше 7 дней.
    """
    from app.services.scenarios import calculate_scenario

    logger.info("Обновление черновых сценариев: %s", datetime.now(timezone.utc).isoformat())
    async with AsyncSessionLocal() as db:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            result = await db.execute(
                select(DevelopmentScenario).where(
                    and_(DevelopmentScenario.status == "draft", DevelopmentScenario.created_at <= cutoff)
                )
            )
            drafts = result.scalars().all()
            if not drafts:
                logger.info("Черновых сценариев старше 7 дней не найдено")
                return

            ok, fail = 0, 0
            for scenario in drafts:
                try:
                    await calculate_scenario(db=db, scenario_id=scenario.id)
                    ok += 1
                except Exception as exc:
                    fail += 1
                    logger.error("Ошибка пересчёта сценария %s: %s", scenario.id, exc)

            await db.commit()
            logger.info("Обновление сценариев: успешно=%d, ошибок=%d", ok, fail)
        except Exception as exc:
            await db.rollback()
            logger.error("Критическая ошибка run_scenario_refresh: %s", exc)
