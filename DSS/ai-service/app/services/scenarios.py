"""
Scenario calculation service.
Generates optimal/stable/risk development scenarios for СКО regions.

KPI сценариев считаются от РЕАЛЬНЫХ данных `indicator_values` (не от захардкоженных
констант): за базу берётся ПОСЛЕДНЕЕ фактическое областное значение показателя (без
трендовой экстраполяции — см. `_compute_kpis`), к которому применяется множитель типа
сценария («рычаг» политики), отмасштабированный по горизонту `target_year − base_year`
(`base_mult ** (H / REFERENCE_HORIZON_YEARS)`), и пользовательские параметры. Набор KPI
переопределён на показатели, по которым есть данные (АПК + демография СКО). Методика —
базовая, подлежит уточнению отраслевыми экспертами.
"""
import logging
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.ai_models import DevelopmentScenario
from app.services import series as series_loader

logger = logging.getLogger(__name__)

# Конфигурация KPI: показатель из indicator_values (по имени) + способ областной
# агрегации при отсутствии прямого областного ряда + единица/подпись для UI.
# agg: 'sum' — экстенсивные величины (продукция, население, инвестиции),
#      'avg' — интенсивные (урожайность, коэффициенты).
# unit — запасная подпись; фактическая единица берётся из справочника units по
# выбранному показателю (реальные единицы, напр. млрд.тенге).
KPI_CONFIG: list[dict] = [
    {"key": "agri_output", "indicator": "Валовая продукция сельского хозяйства",
     "agg": "sum", "unit": "млрд ₸", "label": "Валовая продукция СХ", "non_negative": True},
    {"key": "natural_increase", "indicator": "Естественный прирост населения (человек)",
     "agg": "sum", "unit": "чел.", "label": "Естественный прирост", "non_negative": False},
    {"key": "population", "indicator": "Численность населения (всего)",
     "agg": "sum", "unit": "чел.", "label": "Население", "non_negative": True},
    {"key": "agri_investment", "indicator": "Инвестиции в основной капитал СХ",
     "agg": "sum", "unit": "млрд ₸", "label": "Инвестиции в АПК", "non_negative": True},
]

# Множители KPI по типу сценария (рычаги политики относительно прогнозной базы)
SCENARIO_MULTIPLIERS: dict[str, dict[str, float]] = {
    "optimal": {"agri_output": 1.30, "natural_increase": 1.25, "population": 1.03, "agri_investment": 1.50},
    "stable":  {"agri_output": 1.05, "natural_increase": 1.00, "population": 1.00, "agri_investment": 1.08},
    "risk":    {"agri_output": 0.85, "natural_increase": 0.70, "population": 0.98, "agri_investment": 0.75},
    "custom":  {"agri_output": 1.10, "natural_increase": 1.05, "population": 1.01, "agri_investment": 1.15},
}

# Референсный горизонт, на котором множитель сценария задаёт «полный» эффект.
# Для фактического горизонта H = target_year − base_year эффект масштабируется
# геометрически: effective_mult = base_mult ** (H / REFERENCE_HORIZON_YEARS).
REFERENCE_HORIZON_YEARS = 5
# Верхний предохранитель против чрезмерного компаундинга на длинных горизонтах
# (схема допускает target_year до 2050, а base_year ~2023 → H может дойти до ~27).
MAX_HORIZON_YEARS = 25


async def _resolve_indicator(db: AsyncSession, name: str) -> tuple[UUID, str] | None:
    """Показатель по имени + его единица. Имена могут дублироваться по направлениям —
    берём тот, у которого больше всего одобренных значений (наиболее заполненный ряд)."""
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
    return (row[0], row[1]) if row else None


async def _annual_oblast_series(
    db: AsyncSession, indicator_id: UUID, oblast_id: UUID, agg: str,
) -> list[tuple[int, float]]:
    """Годовой областной ряд показателя (year → value).

    Тонкая обёртка над `app.services.series.annual_series` — единой точкой
    сборки рядов для всего ИИ-модуля: прямые областные значения (level 1), при
    их нехватке — агрегация районов (level 2) с SUM/AVG по типу величины.
    """
    return await series_loader.annual_series(db, indicator_id, None, agg=agg)


async def _compute_kpis(
    db: AsyncSession, oblast_id: UUID, target_year: int, scenario_type: str,
) -> tuple[dict[str, float], dict[str, dict]]:
    """Считает KPI сценария из реальных данных: база × множитель сценария по горизонту.

    Базой берётся ПОСЛЕДНЕЕ фактическое областное значение показателя, а не
    линейная проекция ряда: исторические ряды СКО местами имеют неоднородные
    единицы (напр. население то в тыс., то в абсолюте), из-за чего трендовая
    экстраполяция даёт мусор. Последнее одобренное значение устойчиво к этому.
    Целевой год входит через горизонт H = target_year − base_year: множитель
    сценария (заданный на REFERENCE_HORIZON_YEARS) масштабируется геометрически —
    `base_mult ** (H / REFERENCE_HORIZON_YEARS)` — так дальний горизонт усиливает
    эффект, а H ≤ 0 даёт множитель 1.0 (значение равно базе). base_year у каждого
    KPI свой (последний год его ряда), поэтому горизонт считается по каждому KPI.
    Эффект применяется к модулю базы (`baseline + (mult−1)·|baseline|`), что для
    положительной базы совпадает с `baseline·mult`, а для показателей со знаком
    (отрицательный естественный прирост) не инвертирует смысл optimal/risk.
    Возвращает (kpis, kpi_meta), где meta содержит подпись/единицу/базу для UI.
    """
    multipliers = SCENARIO_MULTIPLIERS.get(scenario_type, SCENARIO_MULTIPLIERS["stable"])
    kpis: dict[str, float] = {}
    meta: dict[str, dict] = {}

    for cfg in KPI_CONFIG:
        resolved = await _resolve_indicator(db, cfg["indicator"])
        if resolved is None:
            continue
        indicator_id, unit = resolved
        series = await _annual_oblast_series(db, indicator_id, oblast_id, cfg["agg"])
        if not series:
            continue
        base_year, baseline = series[-1]  # последнее фактическое значение
        base_mult = multipliers.get(cfg["key"], 1.0)
        # Горизонт: год ≤ базового → без эффекта; сверху ограничен предохранителем.
        horizon = max(0, target_year - base_year)
        horizon = min(horizon, MAX_HORIZON_YEARS)
        effective_mult = base_mult ** (horizon / REFERENCE_HORIZON_YEARS)
        # Эффект применяется к МОДУЛЮ базы в направлении «улучшения»: для положительной
        # базы это тождественно baseline * effective_mult, а для показателей со знаком
        # (напр. отрицательный естественный прирост СКО) optimal (>1) сдвигает значение
        # в плюс, risk (<1) — в минус, без инверсии смысла у отрицательной базы.
        value = baseline + (effective_mult - 1.0) * abs(baseline)
        if cfg["non_negative"]:
            value = max(0.0, value)
        kpis[cfg["key"]] = round(value, 2)
        meta[cfg["key"]] = {
            "label": cfg["label"], "unit": unit or cfg["unit"],
            "baseline": round(baseline, 2), "base_year": base_year, "points": len(series),
            "target_year": target_year, "horizon": horizon,
            "effective_multiplier": round(effective_mult, 4),
        }

    if not kpis:
        logger.warning("KPI из данных не рассчитаны (регион=%s): нет рядов", oblast_id)
    return kpis, meta


def _apply_user_params(kpis: dict[str, float], scenario_params: dict) -> dict[str, float]:
    """Применяет управляющие параметры пользователя («рычаги влияния») поверх KPI."""
    result = dict(kpis)

    # Мультипликатор инвестиций → прямо масштабирует инвестиции в АПК
    if "investment_multiplier" in scenario_params and "agri_investment" in result:
        result["agri_investment"] = round(
            result["agri_investment"] * float(scenario_params["investment_multiplier"]), 2
        )

    # Индекс инфраструктуры → масштабирует валовую продукцию СХ
    if "infrastructure_index" in scenario_params and "agri_output" in result:
        idx = float(scenario_params["infrastructure_index"])
        result["agri_output"] = round(result["agri_output"] * idx, 2)

    # Целевой рост продукции (доля, напр. 0.03 = +3%) → аддитивная надбавка к продукции СХ
    if "gdp_growth_target" in scenario_params and "agri_output" in result:
        target = float(scenario_params["gdp_growth_target"])
        result["agri_output"] = round(result["agri_output"] * (1 + (target - 0.03)), 2)

    # Прирост населения (%/год) → масштабирует население на референсный горизонт
    if "population_growth_rate" in scenario_params and "population" in result:
        rate = float(scenario_params["population_growth_rate"])
        result["population"] = round(
            result["population"] * (1 + rate * REFERENCE_HORIZON_YEARS), 2
        )

    return result


async def calculate_scenario(
    db: AsyncSession,
    scenario_id: UUID,
) -> dict:
    """
    Рассчитывает KPI для сценария и обновляет запись в БД.
    Переводит статус в 'calculated', сохраняет kpis в parameters.
    Возвращает dict с scenario_id, scenario_type и kpis.
    """
    result = await db.execute(
        select(DevelopmentScenario).where(DevelopmentScenario.id == scenario_id)
    )
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise ValueError(f"Сценарий {scenario_id} не найден")

    kpis, kpi_meta = await _compute_kpis(
        db, oblast_id=scenario.region_id, target_year=scenario.target_year,
        scenario_type=scenario.scenario_type,
    )

    # Применяем управляющие параметры, если пользователь их изменил
    scenario_params = (scenario.parameters or {}).get("scenario_params", {})
    if scenario_params:
        kpis = _apply_user_params(kpis, scenario_params)

    scenario.parameters = {
        **(scenario.parameters or {}), "kpis": kpis, "kpi_meta": kpi_meta, "calculated": True,
    }
    flag_modified(scenario, "parameters")
    scenario.status = "calculated"
    await db.flush()
    await db.commit()

    logger.info(
        "Сценарий рассчитан: id=%s type=%s kpis=%s",
        scenario_id, scenario.scenario_type, kpis,
    )
    return {"scenario_id": str(scenario_id), "scenario_type": scenario.scenario_type, "kpis": kpis}


async def generate_all_scenarios(
    db: AsyncSession,
    region_id: UUID,
    target_year: int,
    created_by: UUID,
) -> list[dict]:
    """
    Генерирует и рассчитывает все 3 стандартных сценария для региона.
    Возвращает список dict с результатами расчёта.
    """
    type_names = {
        "optimal": "Оптимистичный сценарий",
        "stable": "Стабильный сценарий",
        "risk": "Пессимистичный (рисковый) сценарий",
    }

    results = []
    for scenario_type in ("optimal", "stable", "risk"):
        kpis, kpi_meta = await _compute_kpis(
            db, oblast_id=region_id, target_year=target_year, scenario_type=scenario_type,
        )
        scenario = DevelopmentScenario(
            name=f"{type_names[scenario_type]} {target_year}",
            scenario_type=scenario_type,
            target_year=target_year,
            region_id=region_id,
            parameters={"kpis": kpis, "kpi_meta": kpi_meta, "calculated": True},
            status="calculated",
            created_by=created_by,
        )
        db.add(scenario)
        await db.flush()
        results.append({
            "scenario_id": str(scenario.id),
            "scenario_type": scenario_type,
            "kpis": kpis,
        })

    logger.info(
        "Сгенерировано %d сценариев для региона %s на %d год",
        len(results), region_id, target_year,
    )
    return results
