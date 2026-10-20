"""
Загрузка исторических рядов показателей из `indicator_values`.

Единая точка правды для всех потребителей (прогнозирование, сценарии,
офлайн-обучение). Ключевое правило: **уровни территориальной иерархии не
смешиваются**. Областной ряд собирается либо из прямых областных значений
(`regions.level = 1`), либо — при их нехватке — агрегацией районов
(`level = 2`, `parent_id = область`) за каждый год. Смешивание уровней в одном
`AVG` даёт разрыв масштаба между годами (в одних годах в выборку попадает и
область, и районы, в других — только районы) и, как следствие, вырожденные
оценки качества на абсолютных счётных показателях.

Ряды в DSS **годовые** (2003–2024), поэтому базовая единица ряда здесь —
пара `(год, значение)`.
"""
import logging
import re
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Способ агрегации районных рядов в областной.
AGG_SUM = "sum"   # экстенсивные (аддитивные) величины: человек, га, тонн, тенге
AGG_AVG = "avg"   # интенсивные (удельные) величины: %, индексы, °C, ц/га

# Единицы, однозначно указывающие на интенсивную величину.
_INTENSIVE_UNITS = {
    "%", "индекс", "балл", "балл (1–5)", "°c", "мм", "ц/га", "кг/чел",
    "на 1000 населения", "на 100 тыс. населения", "коэффициент", "лет",
    "тенге",  # в справочнике это среднемесячная зарплата, а не сумма
}
# Маркеры интенсивности в названии показателя (единица может быть нейтральной).
_INTENSIVE_NAME_RE = re.compile(
    r"средн|на душу|удельн|доля|уровень|индекс|коэффициент|обеспеченност|"
    r"на 1000|на 100 тыс|в расчёте на|в расчете на|\bв %",
    re.IGNORECASE,
)


def aggregation_for(unit: str | None, indicator_name: str | None = None) -> str:
    """Определяет способ свёртки районных значений в областное.

    Интенсивные величины усредняются, экстенсивные суммируются. Признак берётся
    сначала из единицы измерения, затем из названия показателя; по умолчанию —
    сумма (большинство показателей справочника счётные: человек, га, голов, шт.).
    """
    u = (unit or "").strip().lower()
    if u in _INTENSIVE_UNITS or "/" in u:
        return AGG_AVG
    if indicator_name and _INTENSIVE_NAME_RE.search(indicator_name):
        return AGG_AVG
    return AGG_SUM


async def resolve_oblast_id(db: AsyncSession) -> UUID | None:
    """Идентификатор области (`regions.level = 1`). В MVP область одна — СКО."""
    row = (await db.execute(text(
        "SELECT id FROM regions WHERE level = 1 ORDER BY kato_code LIMIT 1"
    ))).first()
    return row[0] if row else None


async def indicator_agg_kind(db: AsyncSession, indicator_id: UUID) -> str:
    """Способ агрегации показателя по его единице измерения и названию."""
    row = (await db.execute(text("""
        SELECT i.name, u.name AS unit
        FROM indicators i
        LEFT JOIN units u ON u.id = i.unit_id
        WHERE i.id = :iid
    """), {"iid": str(indicator_id)})).first()
    if row is None:
        return AGG_SUM
    return aggregation_for(row[1], row[0])


async def annual_series(
    db: AsyncSession,
    indicator_id: UUID,
    region_id: UUID | None = None,
    *,
    agg: str | None = None,
    min_points: int = 3,
) -> list[tuple[int, float]]:
    """Годовой ряд показателя как список пар `(год, значение)`.

    `region_id` задан — ряд строго по этому региону (уровень не важен: берём то,
    что есть по самому региону). `region_id=None` — областной срез: сначала
    прямые значения области (level 1), и только если их меньше `min_points` —
    агрегация районов (level 2 с `parent_id` = область).

    `agg` (`sum`/`avg`) применяется только к районной агрегации; если не задан —
    определяется автоматически по единице измерения показателя.
    """
    if region_id is not None:
        rows = (await db.execute(text("""
            SELECT EXTRACT(YEAR FROM period_start)::int AS yr, value
            FROM indicator_values
            WHERE indicator_id = :iid
              AND region_id = :rid
              AND validation_status = 'approved'
              AND period_type = 'year'
            ORDER BY period_start ASC
        """), {"iid": str(indicator_id), "rid": str(region_id)})).fetchall()
        return [(int(r[0]), float(r[1])) for r in rows if r[1] is not None]

    oblast_id = await resolve_oblast_id(db)
    if oblast_id is not None:
        rows = (await db.execute(text("""
            SELECT EXTRACT(YEAR FROM period_start)::int AS yr, value
            FROM indicator_values
            WHERE indicator_id = :iid
              AND region_id = :oid
              AND validation_status = 'approved'
              AND period_type = 'year'
            ORDER BY period_start ASC
        """), {"iid": str(indicator_id), "oid": str(oblast_id)})).fetchall()
        direct = [(int(r[0]), float(r[1])) for r in rows if r[1] is not None]
        if len(direct) >= min_points:
            return direct

    # Область без собственного ряда — собираем из районов.
    aggfn = "AVG" if (agg or await indicator_agg_kind(db, indicator_id)) == AGG_AVG else "SUM"
    params: dict[str, str] = {"iid": str(indicator_id)}
    parent_filter = ""
    if oblast_id is not None:
        parent_filter = "AND r.parent_id = :oid"
        params["oid"] = str(oblast_id)
    rows = (await db.execute(text(f"""
        SELECT EXTRACT(YEAR FROM iv.period_start)::int AS yr, {aggfn}(iv.value) AS val
        FROM indicator_values iv
        JOIN regions r ON r.id = iv.region_id
        WHERE iv.indicator_id = :iid
          AND iv.validation_status = 'approved'
          AND iv.period_type = 'year'
          AND r.level = 2 {parent_filter}
        GROUP BY yr
        ORDER BY yr ASC
    """), params)).fetchall()
    return [(int(r[0]), float(r[1])) for r in rows if r[1] is not None]
