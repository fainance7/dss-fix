"""Тесты разделения уровней территориальной иерархии при сборке рядов (Д1).

Ключевая проверка: областной ряд НЕ собирается усреднением по всем регионам
подряд. Прежняя реализация делала `AVG(value) GROUP BY period_start` без фильтра
по уровню, из-за чего в годы, где есть и областное значение, и районные, среднее
«проваливалось» к районному масштабу, а в годы только с областным — оставалось
областным. Для абсолютных счётных показателей это давало разрыв масштаба между
соседними годами и вырожденные оценки качества.
"""
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


def _rows(pairs):
    """Результат execute() со списком кортежей (год, значение)."""
    res = MagicMock()
    res.fetchall.return_value = [tuple(p) for p in pairs]
    return res


def _first(value):
    res = MagicMock()
    res.first.return_value = value
    return res


def test_aggregation_for_intensive_units():
    """Интенсивные величины усредняются, экстенсивные суммируются."""
    from app.services.series import AGG_AVG, AGG_SUM, aggregation_for

    assert aggregation_for("%") == AGG_AVG
    assert aggregation_for("ц/га") == AGG_AVG
    assert aggregation_for("кг/чел") == AGG_AVG
    assert aggregation_for("°C") == AGG_AVG
    assert aggregation_for("индекс") == AGG_AVG
    assert aggregation_for("человек") == AGG_SUM
    assert aggregation_for("тыс.га") == AGG_SUM
    assert aggregation_for("гол.") == AGG_SUM


def test_aggregation_for_uses_indicator_name():
    """Нейтральная единица + «средне…» в названии → усреднение."""
    from app.services.series import AGG_AVG, AGG_SUM, aggregation_for

    assert aggregation_for("шт.", "Среднегодовая численность на предприятие") == AGG_AVG
    assert aggregation_for("шт.", "Количество предприятий") == AGG_SUM


@pytest.mark.asyncio
async def test_annual_series_prefers_oblast_level_1():
    """При наличии областного ряда районы не агрегируются вовсе."""
    from app.services.series import annual_series

    oblast_id = uuid4()
    db = AsyncMock()
    db.execute.side_effect = [
        _first((oblast_id,)),                                  # resolve_oblast_id
        _rows([(2022, 100.0), (2023, 110.0), (2024, 120.0)]),  # прямые значения области
    ]

    result = await annual_series(db, uuid4(), None)

    assert result == [(2022, 100.0), (2023, 110.0), (2024, 120.0)]
    assert db.execute.await_count == 2, "лишний запрос = районы агрегировались напрасно"


@pytest.mark.asyncio
async def test_annual_series_falls_back_to_districts():
    """Областного ряда нет (< 3 точек) → агрегация районов уровня 2."""
    from app.services.series import annual_series

    oblast_id = uuid4()
    db = AsyncMock()
    db.execute.side_effect = [
        _first((oblast_id,)),                 # resolve_oblast_id
        _rows([(2024, 5.0)]),                 # только одна точка по области
        _first(("Численность населения", "человек")),  # indicator_agg_kind
        _rows([(2022, 900.0), (2023, 950.0), (2024, 1000.0)]),  # SUM по районам
    ]

    result = await annual_series(db, uuid4(), None)

    assert result == [(2022, 900.0), (2023, 950.0), (2024, 1000.0)]
    # Запрос агрегации должен фильтровать уровень 2 и родителя — это и есть Д1.
    sql = str(db.execute.await_args_list[-1].args[0])
    assert "r.level = 2" in sql
    assert "r.parent_id = :oid" in sql
    assert "SUM(iv.value)" in sql


@pytest.mark.asyncio
async def test_annual_series_district_uses_avg_for_intensive():
    """Для интенсивного показателя районы усредняются, а не суммируются."""
    from app.services.series import annual_series

    oblast_id = uuid4()
    db = AsyncMock()
    db.execute.side_effect = [
        _first((oblast_id,)),
        _rows([]),
        _first(("Уровень безработицы", "%")),
        _rows([(2023, 4.8), (2024, 4.6)]),
    ]

    await annual_series(db, uuid4(), None)

    sql = str(db.execute.await_args_list[-1].args[0])
    assert "AVG(iv.value)" in sql
    assert "SUM(iv.value)" not in sql


@pytest.mark.asyncio
async def test_annual_series_for_region_is_direct():
    """Ряд конкретного района берётся напрямую, без обращения к области."""
    from app.services.series import annual_series

    db = AsyncMock()
    db.execute.side_effect = [_rows([(2023, 7.0), (2024, 8.0)])]

    result = await annual_series(db, uuid4(), uuid4())

    assert result == [(2023, 7.0), (2024, 8.0)]
    assert db.execute.await_count == 1
    sql = str(db.execute.await_args_list[0].args[0])
    assert "region_id = :rid" in sql


@pytest.mark.asyncio
async def test_annual_series_takes_only_yearly_rows():
    """Годовой ряд не смешивается с месячными/декадными строками того же показателя:
    все три выборки (район, область, агрегация районов) фильтруют period_type='year'."""
    from app.services.series import annual_series

    db = AsyncMock()
    db.execute.side_effect = [_rows([(2023, 7.0)])]
    await annual_series(db, uuid4(), uuid4())
    assert "period_type = 'year'" in str(db.execute.await_args_list[0].args[0])

    db = AsyncMock()
    db.execute.side_effect = [_first((uuid4(),)), _rows([]), _first(("тонна", "x")), _rows([(2023, 1.0)])]
    try:
        await annual_series(db, uuid4(), None)
    except Exception:
        pass  # порядок служебных запросов не важен — проверяем только SQL выборок значений
    sqls = [str(c.args[0]) for c in db.execute.await_args_list if "indicator_values" in str(c.args[0])]
    assert sqls and all("period_type = 'year'" in s for s in sqls)
